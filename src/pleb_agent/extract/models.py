"""The structured result a model returns. Normalization turns it into weekday windows."""

from pydantic import BaseModel, Field


class RawWindow(BaseModel):
    """One happy hour period exactly as the source states it."""

    days: str = Field(description='Days as written, e.g. "Mon-Fri", "Daily", "Sunday", "Thu, Fri & Sat".')
    start: str | None = Field(
        default=None, description='Start time as written, e.g. "4", "4pm", "16:00", "noon". Null if all day.'
    )
    end: str | None = Field(
        default=None, description='End time as written, e.g. "7", "7pm", "1am", "close". Null if all day.'
    )
    all_day: bool = Field(default=False, description="True when the happy hour runs all day on these days.")


class Deal(BaseModel):
    item: str = Field(description='What is discounted, e.g. "Draft beer", "Oysters".')
    price: float | None = Field(default=None, description="Price in dollars, if stated.")
    note: str | None = Field(default=None, description='Qualifier such as "each" or "half off".')


class MenuExtraction(BaseModel):
    is_happy_hour: bool = Field(description="True only if the content describes a recurring happy hour.")
    windows: list[RawWindow] = Field(default_factory=list)
    deals: list[Deal] = Field(default_factory=list)
    notes: str | None = Field(default=None, description='Conditions such as "bar area only".')
    confidence: float = Field(ge=0, le=1, description="How sure you are that this reading is correct.")
