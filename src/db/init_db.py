from src.db.session import sync_engine
from src.db.models import Base


def init_db() -> None:
	Base.metadata.create_all(bind=sync_engine)
