from app.infrastructure.persistence.ai_memory_repository import SqlAlchemyAiMemoryRepository
from app.presentation.routes.proposals import _clear_pending_proposal


def test_applying_a_hologram_clears_the_ais_stored_pending_proposal(diagram_repo):
    memory = SqlAlchemyAiMemoryRepository(diagram_repo.db)
    memory.save_pending("diag-pending-1", [{"command_type": "CREATE_CLASS", "name": "Producto"}], 3, "usr-1")
    assert memory.get("diag-pending-1").has_live_pending()

    _clear_pending_proposal(diagram_repo, "diag-pending-1")

    diagram_repo.db.expire_all()
    assert not memory.get("diag-pending-1").has_live_pending()
