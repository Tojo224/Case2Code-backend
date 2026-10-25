import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.core.config import settings
settings.DATABASE_URL = "sqlite:///:memory:"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# In-memory SQLite for super fast, isolated tests
TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Point core database engine and sessionmaker to test in-memory SQLite
import app.core.database as db_module
db_module.engine = engine
db_module.SessionLocal = TestingSessionLocal

from app.core.database import Base, get_db
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def diagram_repo(db_session):
    return SqlAlchemyDiagramRepository(db_session)


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sample_document():
    return CanonicalUmlDocument(
        id="diag-test-1",
        name="Peluqueria Test",
        description="Sample diagram for tests",
        version=1,
        classes=[],
        relationships=[],
    )

