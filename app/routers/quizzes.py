"""
Turns a document's approved questions into a publishable quiz — the last
step of the "doc -> quiz" workflow.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_teacher_or_admin
from app.models.document import Document, Question, Quiz, QuizQuestion
from app.models.user import User
from app.schemas.quiz import (
    PublicQuestionOut,
    PublicQuizOut,
    QuizCreateRequest,
    QuizOut,
    QuizResult,
    QuizSubmission,
)

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


@router.get("", response_model=list[PublicQuizOut])
async def list_published_quizzes(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PublicQuizOut]:
    """The student-facing homepage list — every published quiz, regardless
    of who created it."""
    published = (await db.scalars(select(Quiz).where(Quiz.is_published.is_(True)))).all()

    results: list[PublicQuizOut] = []
    for quiz in published:
        count = len(
            (await db.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz.id))).all()
        )
        results.append(
            PublicQuizOut(id=str(quiz.id), title=quiz.title, language=quiz.language, question_count=count)
        )
    return results


@router.get("/{quiz_id}/take", response_model=list[PublicQuestionOut])
async def get_quiz_for_taking(
    quiz_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PublicQuestionOut]:
    """Questions in quiz order, correct answers stripped out — this is what
    a student actually sees while taking the quiz."""
    quiz = await db.scalar(select(Quiz).where(Quiz.id == quiz_id, Quiz.is_published.is_(True)))
    if quiz is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")

    ordered_questions = await _ordered_quiz_questions(quiz_id, db)
    return [
        PublicQuestionOut(id=str(q.id), type=q.type.value, text=q.text, options=q.options)
        for q in ordered_questions
    ]


@router.post("/{quiz_id}/submit", response_model=QuizResult)
async def submit_quiz_answers(
    quiz_id: uuid.UUID,
    payload: QuizSubmission,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QuizResult:
    quiz = await db.scalar(select(Quiz).where(Quiz.id == quiz_id, Quiz.is_published.is_(True)))
    if quiz is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")

    questions = await _ordered_quiz_questions(quiz_id, db)
    correct_ids = [
        str(q.id)
        for q in questions
        if _answers_match(payload.answers.get(str(q.id)), q.correct_answer)
    ]
    return QuizResult(score=len(correct_ids), total=len(questions), correct_question_ids=correct_ids)


async def _ordered_quiz_questions(quiz_id: uuid.UUID, db: AsyncSession) -> list[Question]:
    quiz_questions = (
        await db.scalars(
            select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.order_index)
        )
    ).all()
    question_ids = [qq.question_id for qq in quiz_questions]
    if not question_ids:
        return []
    questions_by_id = {
        q.id: q for q in (await db.scalars(select(Question).where(Question.id.in_(question_ids)))).all()
    }
    return [questions_by_id[qq.question_id] for qq in quiz_questions if qq.question_id in questions_by_id]


def _answers_match(submitted: object, correct: object) -> bool:
    """Order-independent, case/whitespace-insensitive comparison. Handles
    both a plain string answer and a list (multiple_select)."""
    if submitted is None or correct is None:
        return False

    def _normalize(value: object) -> set[str]:
        items = value if isinstance(value, list) else [value]
        return {str(item).strip().lower() for item in items}

    return _normalize(submitted) == _normalize(correct)


@router.post("", response_model=QuizOut, status_code=status.HTTP_201_CREATED)
async def create_quiz_from_document(
    payload: QuizCreateRequest,
    user: User = Depends(require_teacher_or_admin),
    db: AsyncSession = Depends(get_db),
) -> QuizOut:
    try:
        document_id = uuid.UUID(payload.document_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document_id")

    document = await db.scalar(select(Document).where(Document.id == document_id))
    if document is None or document.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    approved_questions = (
        await db.scalars(
            select(Question).where(
                Question.document_id == document_id, Question.is_approved.is_(True)
            )
        )
    ).all()

    if not approved_questions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No approved questions for this document yet — review and approve some first",
        )

    quiz = Quiz(
        owner_id=user.id,
        document_id=document_id,
        title=payload.title,
        language=payload.language,
        time_limit_sec=payload.time_limit_sec,
        passing_score=payload.passing_score,
    )
    db.add(quiz)
    await db.flush()  # assigns quiz.id

    for index, question in enumerate(approved_questions):
        db.add(QuizQuestion(quiz_id=quiz.id, question_id=question.id, order_index=index, points=1))

    await db.commit()

    return QuizOut(
        id=str(quiz.id),
        title=quiz.title,
        language=quiz.language,
        time_limit_sec=quiz.time_limit_sec,
        passing_score=quiz.passing_score,
        is_published=quiz.is_published,
        question_count=len(approved_questions),
    )


@router.post("/{quiz_id}/publish", response_model=QuizOut)
async def publish_quiz(
    quiz_id: uuid.UUID,
    user: User = Depends(require_teacher_or_admin),
    db: AsyncSession = Depends(get_db),
) -> QuizOut:
    quiz = await db.scalar(select(Quiz).where(Quiz.id == quiz_id))
    if quiz is None or quiz.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")

    quiz.is_published = True
    await db.commit()

    count = len((await db.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id))).all())
    return QuizOut(
        id=str(quiz.id),
        title=quiz.title,
        language=quiz.language,
        time_limit_sec=quiz.time_limit_sec,
        passing_score=quiz.passing_score,
        is_published=quiz.is_published,
        question_count=count,
    )
