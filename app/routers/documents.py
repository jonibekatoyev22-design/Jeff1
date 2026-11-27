"""
Upload a document, extract its existing questions with OpenAI, and review
the results. This is the core "doc -> quiz" workflow.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_teacher_or_admin
from app.db.session import AsyncSessionLocal
from app.models.document import AnswerConfidence, Document, DocumentStatus, Question, QuestionType
from app.models.user import User
from app.schemas.document import DocumentOut, JsonImportRequest, QuestionOut
from app.services.ai_extraction import ExtractionError, extract_questions_from_text
from app.services.storage import save_upload
from app.services.text_extraction import UnsupportedFileTypeError, extract_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED_MIME_TYPES = {
    "text/plain",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile,
    user: User = Depends(require_teacher_or_admin),
    db: AsyncSession = Depends(get_db),
) -> Document:
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type {file.content_type}. Allowed: .txt, .pdf, .docx",
        )

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    file_path = save_upload(contents, file.filename or "upload")

    document = Document(
        owner_id=user.id,
        filename=file.filename or "upload",
        file_path=file_path,
        mime_type=file.content_type,
        status=DocumentStatus.UPLOADED,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


@router.post("/import-json", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def import_json_document(
    payload: JsonImportRequest,
    user: User = Depends(require_teacher_or_admin),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Skip the document-upload + AI-extraction pipeline entirely: the
    person already has questions and answers (e.g. converted a test to
    JSON themselves) and wants them turned straight into a reviewable
    document, no OpenAI call and no API key required."""
    if not payload.questions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="questions list is empty")

    valid_types = {t.value for t in QuestionType}
    for raw in payload.questions:
        if raw.type not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid question type '{raw.type}'. Allowed: {', '.join(sorted(valid_types))}",
            )

    document = Document(
        owner_id=user.id,
        filename=payload.title or "imported.json",
        file_path="(imported-json)",
        mime_type="application/json",
        status=DocumentStatus.READY,
    )
    db.add(document)
    await db.flush()  # assigns document.id without ending the transaction

    for raw in payload.questions:
        db.add(
            Question(
                document_id=document.id,
                owner_id=user.id,
                type=QuestionType(raw.type),
                text=raw.text,
                options=raw.options,
                correct_answer=raw.correct_answer,
                explanation=raw.explanation,
                confidence=AnswerConfidence.VERIFIED,
                source_excerpt=None,
                is_approved=True,
            )
        )

    await db.commit()
    await db.refresh(document)
    return document


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    return await _get_owned_document(document_id, user, db)


@router.post("/{document_id}/extract", status_code=status.HTTP_202_ACCEPTED)
async def extract_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_teacher_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    document = await _get_owned_document(document_id, user, db)

    if document.status == DocumentStatus.PROCESSING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already processing")

    document.status = DocumentStatus.PROCESSING
    document.error_message = None
    await db.commit()

    # MVP: runs in a FastAPI background task on the same process. Swap this
    # call for a Celery .delay(...) once Stage 2 needs to scale extraction
    # across workers — the function body itself does not need to change.
    background_tasks.add_task(_run_extraction, document_id)

    return {"detail": "Extraction started", "document_id": str(document_id)}


@router.get("/{document_id}/questions", response_model=list[QuestionOut])
async def list_document_questions(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Question]:
    await _get_owned_document(document_id, user, db)  # authorization check
    result = await db.scalars(select(Question).where(Question.document_id == document_id))
    return list(result.all())


async def _get_owned_document(document_id: uuid.UUID, user: User, db: AsyncSession) -> Document:
    document = await db.scalar(select(Document).where(Document.id == document_id))
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if document.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your document")
    return document


async def _run_extraction(document_id: uuid.UUID) -> None:
    """Runs outside the request/response cycle, so it opens its own DB
    session rather than reusing the request-scoped one."""
    async with AsyncSessionLocal() as db:
        document = await db.scalar(select(Document).where(Document.id == document_id))
        if document is None:
            return

        try:
            text = extract_text(document.file_path, document.mime_type)
            raw_questions = extract_questions_from_text(text)

            for raw in raw_questions:
                db.add(
                    Question(
                        document_id=document.id,
                        owner_id=document.owner_id,
                        type=QuestionType(raw["type"]),
                        text=raw["text"],
                        options=raw["options"],
                        correct_answer=raw["correct_answer"],
                        explanation=raw["explanation"],
                        confidence=AnswerConfidence(raw["confidence"]),
                        source_excerpt=raw["source_excerpt"],
                    )
                )

            document.status = DocumentStatus.READY
            await db.commit()

        except (UnsupportedFileTypeError, ExtractionError) as exc:
            logger.warning("Extraction failed for document %s: %s", document_id, exc)
            document.status = DocumentStatus.FAILED
            document.error_message = str(exc)
            await db.commit()
        except Exception as exc:  # pragma: no cover - unexpected failure safety net
            logger.exception("Unexpected error extracting document %s", document_id)
            document.status = DocumentStatus.FAILED
            document.error_message = "Unexpected error during extraction"
            await db.commit()
