"""
Tests for the student-facing quiz experience: browsing published quizzes,
taking one (no correct answers leaked), and submitting answers for a
score. Also covers that only teacher/admin accounts can author or publish
quizzes — students may only take them.
"""


async def _auth_headers(client, user):
    response = await client.post(
        "/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _publish_sample_quiz(client, teacher_headers) -> str:
    payload = {
        "title": "Geography basics",
        "questions": [
            {
                "type": "single_choice",
                "text": "Capital of France?",
                "options": ["Paris", "London", "Berlin"],
                "correct_answer": "Paris",
            },
            {
                "type": "multiple_select",
                "text": "Which are planets?",
                "options": ["Mars", "Moon", "Venus"],
                "correct_answer": ["Mars", "Venus"],
            },
        ],
    }
    document = (
        await client.post("/documents/import-json", json=payload, headers=teacher_headers)
    ).json()
    quiz = (
        await client.post(
            "/quizzes",
            json={"document_id": document["id"], "title": payload["title"]},
            headers=teacher_headers,
        )
    ).json()
    published = await client.post(f"/quizzes/{quiz['id']}/publish", headers=teacher_headers)
    assert published.status_code == 200
    return quiz["id"]


class TestRoleRestrictions:
    async def test_student_cannot_upload_document(self, client, student_user):
        headers = await _auth_headers(client, student_user)
        response = await client.post(
            "/documents", headers=headers, files={"file": ("sample.txt", b"content", "text/plain")}
        )
        assert response.status_code == 403

    async def test_student_cannot_import_json(self, client, student_user):
        headers = await _auth_headers(client, student_user)
        response = await client.post(
            "/documents/import-json",
            headers=headers,
            json={"title": "x", "questions": [{"type": "single_choice", "text": "Q?"}]},
        )
        assert response.status_code == 403

    async def test_student_cannot_publish_quiz(self, client, registered_user, student_user):
        teacher_headers = await _auth_headers(client, registered_user)
        quiz_id = await _publish_sample_quiz(client, teacher_headers)

        student_headers = await _auth_headers(client, student_user)
        response = await client.post(f"/quizzes/{quiz_id}/publish", headers=student_headers)
        assert response.status_code == 403


class TestQuizTaking:
    async def test_student_sees_published_quiz_in_list(self, client, registered_user, student_user):
        teacher_headers = await _auth_headers(client, registered_user)
        await _publish_sample_quiz(client, teacher_headers)

        student_headers = await _auth_headers(client, student_user)
        response = await client.get("/quizzes", headers=student_headers)
        assert response.status_code == 200
        titles = [q["title"] for q in response.json()]
        assert "Geography basics" in titles

    async def test_taking_a_quiz_hides_correct_answers(self, client, registered_user, student_user):
        teacher_headers = await _auth_headers(client, registered_user)
        quiz_id = await _publish_sample_quiz(client, teacher_headers)

        student_headers = await _auth_headers(client, student_user)
        response = await client.get(f"/quizzes/{quiz_id}/take", headers=student_headers)
        assert response.status_code == 200
        questions = response.json()
        assert len(questions) == 2
        for q in questions:
            assert "correct_answer" not in q

    async def test_submit_quiz_scores_correctly(self, client, registered_user, student_user):
        teacher_headers = await _auth_headers(client, registered_user)
        quiz_id = await _publish_sample_quiz(client, teacher_headers)

        student_headers = await _auth_headers(client, student_user)
        questions = (await client.get(f"/quizzes/{quiz_id}/take", headers=student_headers)).json()

        answers = {}
        for q in questions:
            if q["text"].startswith("Capital"):
                answers[q["id"]] = "paris"  # case-insensitive match
            else:
                answers[q["id"]] = ["Venus", "mars"]  # order/case independent

        response = await client.post(
            f"/quizzes/{quiz_id}/submit", json={"answers": answers}, headers=student_headers
        )
        assert response.status_code == 200
        result = response.json()
        assert result["score"] == 2
        assert result["total"] == 2

    async def test_submit_with_wrong_answer_is_not_counted(
        self, client, registered_user, student_user
    ):
        teacher_headers = await _auth_headers(client, registered_user)
        quiz_id = await _publish_sample_quiz(client, teacher_headers)

        student_headers = await _auth_headers(client, student_user)
        questions = (await client.get(f"/quizzes/{quiz_id}/take", headers=student_headers)).json()
        answers = {q["id"]: "wrong answer" for q in questions}

        response = await client.post(
            f"/quizzes/{quiz_id}/submit", json={"answers": answers}, headers=student_headers
        )
        assert response.json()["score"] == 0

    async def test_unpublished_quiz_not_listed_or_takeable(self, client, registered_user):
        teacher_headers = await _auth_headers(client, registered_user)
        payload = {
            "title": "Draft quiz",
            "questions": [{"type": "single_choice", "text": "Q?", "correct_answer": "A"}],
        }
        document = (
            await client.post("/documents/import-json", json=payload, headers=teacher_headers)
        ).json()
        quiz = (
            await client.post(
                "/quizzes",
                json={"document_id": document["id"], "title": payload["title"]},
                headers=teacher_headers,
            )
        ).json()
        # intentionally not published

        listing = await client.get("/quizzes", headers=teacher_headers)
        assert "Draft quiz" not in [q["title"] for q in listing.json()]

        take = await client.get(f"/quizzes/{quiz['id']}/take", headers=teacher_headers)
        assert take.status_code == 404
