from app import models  # noqa: F401 - import so SQLAlchemy sees the models
from app.database import Base, engine

Base.metadata.create_all(bind=engine)
print("Tables created successfully.")
