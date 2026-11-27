from pydantic import BaseModel, Field, field_validator


class PublicQuizOut(BaseModel):
    """What a student sees in the quiz list — no document/owner details,
    no questions yet."""

    id: str
    title: str
    language: str
    question_count: int

    model_config = {"from_attributes": True}

    @field_validator("id", mode="before")
    @classmethod
    def stringify(cls, value):
        return str(value)


class PublicQuestionOut(BaseModel):
    """A question as shown while taking a quiz — no correct_answer, no
    explanation, no confidence. Those stay server-side until submission."""

    id: str
    type: str
    text: str
    options: list | None

    model_config = {"from_attributes": True}

    @field_validator("id", "type", mode="before")
    @classmethod
    def stringify(cls, value):
        return str(value.value) if hasattr(value, "value") else str(value)


class QuizSubmission(BaseModel):
    # question_id (str) -> the student's answer, in the same shape as
    # Question.correct_answer (a string, a list for multiple_select, etc.)
    answers: dict[str, str | list | None]


class QuizResult(BaseModel):
    score: int
    total: int
    correct_question_ids: list[str]


class QuizCreateRequest(BaseModel):
    document_id: str
    title: str = Field(min_length=1, max_length=255)
    language: str = "en"
    time_limit_sec: int | None = None
    passing_score: int | None = None


class QuizOut(BaseModel):
    id: str
    title: str
    language: str
    time_limit_sec: int | None
    passing_score: int | None
    is_published: bool
    question_count: int = 0

    model_config = {"from_attributes": True}

    @field_validator("id", mode="before")
    @classmethod
    def stringify(cls, value):
        return str(value)
