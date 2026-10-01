from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
FieldID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
Score = Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EncodeRequest(StrictModel):
    texts: list[Text] = Field(min_length=1, max_length=64)


class SimilarityRequest(EncodeRequest):
    top_k: int = Field(default=5, ge=1, le=1000, strict=True)
    language: Literal["auto", "zh", "en"] = "auto"


class SearchRequest(SimilarityRequest):
    top_k: int = Field(default=20, ge=1, le=1000, strict=True)


class FieldDefinition(StrictModel):
    field_id: FieldID
    canonical_text: Text | None = None
    key_zh: Text | None = None
    key_en: Text | None = None
    source_key: Text | None = None
    aliases: list[Annotated[str, StringConstraints(min_length=1, max_length=512)]] = Field(
        default_factory=list, max_length=64
    )
    field_type: Annotated[str, StringConstraints(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def require_complete_names(self):
        if (self.key_zh is None) != (self.key_en is None):
            raise ValueError("key_zh and key_en must be supplied together")
        if self.canonical_text is None and self.key_en is None:
            raise ValueError("canonical_text or bilingual keys are required")
        return self


class Feedback(StrictModel):
    query_text: Text
    positive_field_id: FieldID
    retrieved_candidates: list[tuple[FieldID, Score]] = Field(default_factory=list, max_length=1000)
    source: Literal["human_confirmed", "agent_confirmed"]
    language_pair: Literal["en-en", "zh-zh", "en-zh", "zh-en"] | None = None
