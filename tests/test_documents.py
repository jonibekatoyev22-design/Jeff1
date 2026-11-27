"""
Tests for the core "doc -> quiz" workflow: upload, extraction (OpenAI
calls mocked — no network access in tests), review/approve, and
publishing a quiz from approved questions.
"""
import uuid
from unittest.mock import patch

import pytest

from app.core.security import create_access_token
from app.models.document import Document, DocumentStatus
from app.models.user import GlobalRole, User


async def _auth_headers(client, registered_user):
    response = await client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


SAMPLE_EXTRACTED_QUESTIONS = [
    {
        "type": "single_choice",
        "text": "What is the capital of France?",
        "options": ["Paris", "London", "Berlin"],
        "correct_answer": "Paris",
        "explanation": "Paris is the capital of France.",
        "confidence": "verified",
        "source_excerpt": "Q1. What is the capital of France? Answer: Paris",
    },
    {
        "type": "true_false",
        "text": "The Earth is flat.",
        "options": None,
        "correct_answer": "False",
        "explanation": None,
        "confidence": "ai_inferred",
        "source_excerpt": "The Earth is flat.",
    },
]


@pytest.fixture(autouse=True)
def _local_storage(tmp_path, monkeypatch):
    """Redirects file storage to a temp directory instead of /var/data."""
    import app.services.storage as storage

    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path)


class TestUpload:
    async def test_upload_txt_document(self, client, registered_user):
        headers = await _auth_headers(client, registered_user)
        response = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.txt", b"Q1. What is 2+2? Answer: 4", "text/plain")},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["filename"] == "sample.txt"
        assert body["status"] == "uploaded"

    async def test_upload_rejects_unsupported_type(self, client, registered_user):
        headers = await _auth_headers(client, registered_user)
        response = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.exe", b"binary", "application/octet-stream")},
        )
        assert response.status_code == 415

    async def test_upload_rejects_empty_file(self, client, registered_user):
        headers = await _auth_headers(client, registered_user)
        response = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert response.status_code == 400

    async def test_upload_requires_auth(self, client):
        response = await client.post(
            "/documents", files={"file": ("sample.txt", b"content", "text/plain")}
        )
        assert response.status_code == 401


class TestExtraction:
    async def test_extract_populates_questions_on_success(self, client, db_session, registered_user):
        headers = await _auth_headers(client, registered_user)
        upload = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.txt", b"Q1. What is the capital of France? Answer: Paris", "text/plain")},
        )
        document_id = upload.json()["id"]

        with patch(
            "app.routers.documents.extract_questions_from_text",
            return_value=SAMPLE_EXTRACTED_QUESTIONS,
        ):
            response = await client.post(f"/documents/{document_id}/extract", headers=headers)
            assert response.status_code == 202

        status_response = await client.get(f"/documents/{document_id}", headers=headers)
        assert status_response.json()["status"] == "ready"

        questions_response = await client.get(f"/documents/{document_id}/questions", headers=headers)
        questions = questions_response.json()
        assert len(questions) == 2
        assert {q["confidence"] for q in questions} == {"verified", "ai_inferred"}
        assert all(q["is_approved"] is False for q in questions)  # nothing auto-approved

    async def test_extract_marks_document_failed_on_extraction_error(
        self, client, db_session, registered_user
    ):
        headers = await _auth_headers(client, registered_user)
        upload = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.txt", b"irrelevant content", "text/plain")},
        )
        document_id = upload.json()["id"]

        from app.services.ai_extraction import ExtractionError

        with patch(
            "app.routers.documents.extract_questions_from_text",
            side_effect=ExtractionError("OpenAI request failed after retries"),
        ):
            response = await client.post(f"/documents/{document_id}/extract", headers=headers)
            assert response.status_code == 202

        status_response = await client.get(f"/documents/{document_id}", headers=headers)
        body = status_response.json()
        assert body["status"] == "failed"
        assert "OpenAI" in body["error_message"]

    async def test_cannot_extract_someone_elses_document(self, client, db_session, registered_user):
        headers = await _auth_headers(client, registered_user)
        upload = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.txt", b"content", "text/plain")},
        )
        document_id = upload.json()["id"]

        other_user = User(
            email="other@example.com",
            password_hash="x",
            is_active=True,
            is_approved=True,
            role=GlobalRole.TEACHER,
        )
        db_session.add(other_user)
        await db_session.commit()
        other_token = create_access_token(str(other_user.id))

        response = await client.post(
            f"/documents/{document_id}/extract",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 403


class TestReviewAndPublish:
    async def test_approve_question_and_publish_quiz(self, client, db_session, registered_user):
        headers = await _auth_headers(client, registered_user)
        upload = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.txt", b"Q1. Capital of France? Answer: Paris", "text/plain")},
        )
        document_id = upload.json()["id"]

        with patch(
            "app.routers.documents.extract_questions_from_text",
            return_value=SAMPLE_EXTRACTED_QUESTIONS,
        ):
            await client.post(f"/documents/{document_id}/extract", headers=headers)

        questions = (await client.get(f"/documents/{document_id}/questions", headers=headers)).json()
        first_question_id = questions[0]["id"]

        approve_response = await client.patch(
            f"/questions/{first_question_id}", json={"is_approved": True}, headers=headers
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["is_approved"] is True

        quiz_response = await client.post(
            "/quizzes",
            json={"document_id": document_id, "title": "France Geography Quiz"},
            headers=headers,
        )
        assert quiz_response.status_code == 201
        quiz_body = quiz_response.json()
        assert quiz_body["question_count"] == 1
        assert quiz_body["is_published"] is False

        publish_response = await client.post(
            f"/quizzes/{quiz_body['id']}/publish", headers=headers
        )
        assert publish_response.status_code == 200
        assert publish_response.json()["is_published"] is True

    async def test_cannot_create_quiz_with_no_approved_questions(
        self, client, db_session, registered_user
    ):
        headers = await _auth_headers(client, registered_user)
        upload = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.txt", b"content", "text/plain")},
        )
        document_id = upload.json()["id"]

        response = await client.post(
            "/quizzes", json={"document_id": document_id, "title": "Empty Quiz"}, headers=headers
        )
        assert response.status_code == 400

    async def test_edit_question_text_before_approving(self, client, db_session, registered_user):
        headers = await _auth_headers(client, registered_user)
        upload = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.txt", b"content", "text/plain")},
        )
        document_id = upload.json()["id"]

        with patch(
            "app.routers.documents.extract_questions_from_text",
            return_value=SAMPLE_EXTRACTED_QUESTIONS,
        ):
            await client.post(f"/documents/{document_id}/extract", headers=headers)

        questions = (await client.get(f"/documents/{document_id}/questions", headers=headers)).json()
        question_id = questions[0]["id"]

        response = await client.patch(
            f"/questions/{question_id}",
            json={"text": "What is the capital city of France?"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["text"] == "What is the capital city of France?"

    async def test_delete_question(self, client, db_session, registered_user):
        headers = await _auth_headers(client, registered_user)
        upload = await client.post(
            "/documents",
            headers=headers,
            files={"file": ("sample.txt", b"content", "text/plain")},
        )
        document_id = upload.json()["id"]

        with patch(
            "app.routers.documents.extract_questions_from_text",
            return_value=SAMPLE_EXTRACTED_QUESTIONS,
        ):
            await client.post(f"/documents/{document_id}/extract", headers=headers)

        questions = (await client.get(f"/documents/{document_id}/questions", headers=headers)).json()
        question_id = questions[0]["id"]

        delete_response = await client.delete(f"/questions/{question_id}", headers=headers)
        assert delete_response.status_code == 204

        remaining = (await client.get(f"/documents/{document_id}/questions", headers=headers)).json()
        assert question_id not in [q["id"] for q in remaining]


class TestImportJson:
    """Direct JSON import: no file upload, no AI extraction, no OpenAI key
    needed — the person already has questions+answers in JSON form."""

    async def test_import_json_creates_ready_document_with_approved_questions(
        self, client, registered_user
    ):
        headers = await _auth_headers(client, registered_user)
        payload = {
            "title": "My ready-made quiz",
            "questions": [
                {
                    "type": "single_choice",
                    "text": "2 + 2 = ?",
                    "options": ["3", "4", "5"],
                    "correct_answer": "4",
                    "explanation": "Basic arithmetic.",
                },
                {
                    "type": "true_false",
                    "text": "The sky is blue.",
                    "correct_answer": "True",
                },
            ],
        }

        response = await client.post("/documents/import-json", json=payload, headers=headers)
        assert response.status_code == 201
        document = response.json()
        assert document["status"] == "ready"
        assert document["filename"] == "My ready-made quiz"

        questions = (
            await client.get(f"/documents/{document['id']}/questions", headers=headers)
        ).json()
        assert len(questions) == 2
        assert all(q["is_approved"] is True for q in questions)
        assert all(q["confidence"] == "verified" for q in questions)

    async def test_import_json_rejects_empty_questions(self, client, registered_user):
        headers = await _auth_headers(client, registered_user)
        response = await client.post(
            "/documents/import-json",
            json={"title": "Empty", "questions": []},
            headers=headers,
        )
        assert response.status_code == 400

    async def test_import_json_rejects_invalid_question_type(self, client, registered_user):
        headers = await _auth_headers(client, registered_user)
        response = await client.post(
            "/documents/import-json",
            json={
                "title": "Bad type",
                "questions": [{"type": "essay", "text": "Explain gravity."}],
            },
            headers=headers,
        )
        assert response.status_code == 400

    async def test_import_json_can_be_published_as_quiz_immediately(
        self, client, registered_user
    ):
        headers = await _auth_headers(client, registered_user)
        payload = {
            "title": "Instant quiz",
            "questions": [{"type": "single_choice", "text": "Q?", "correct_answer": "A"}],
        }
        document = (
            await client.post("/documents/import-json", json=payload, headers=headers)
        ).json()

        quiz_response = await client.post(
            "/quizzes",
            json={"document_id": document["id"], "title": "Instant quiz"},
            headers=headers,
        )
        assert quiz_response.status_code == 201
