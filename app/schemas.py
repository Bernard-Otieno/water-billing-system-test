from pydantic import BaseModel


class ReadingSubmission(BaseModel):
    account_number: str
    current_reading: float
    confirm_overwrite: bool = False
