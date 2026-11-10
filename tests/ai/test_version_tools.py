import pytest

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.versioning import DiagramChange, VersionAuthorType, VersionKind
from app.infrastructure.ai import version_tools as vt
from tests.ai.fakes import FakeVersionService, make_version

DOC = CanonicalUmlDocument(id="d", name="n")


@pytest.mark.parametrize(
    "text,kind",
    [
        ("@ai ¿qué cambió?", vt.VersionIntentKind.HISTORY),
        ("mostrame el historial", vt.VersionIntentKind.HISTORY),
        ("@ai guardá una versión", vt.VersionIntentKind.CHECKPOINT),
        ("creá un punto de restauración", vt.VersionIntentKind.CHECKPOINT),
        ("@ai volvé a la versión 3", vt.VersionIntentKind.RESTORE),
        ("restaurá la anterior", vt.VersionIntentKind.RESTORE),
        ("deshacé lo último que hiciste", vt.VersionIntentKind.RESTORE),
    ],
)
def test_detect_intent(text, kind):
    assert vt.detect_version_intent(text).kind == kind


@pytest.mark.parametrize("text", ["creá la clase Cliente", "volvé a crear Producto", "hola"])
def test_non_version_messages_have_no_intent(text):
    assert vt.detect_version_intent(text) is None


def test_restore_number_target():
    intent = vt.detect_version_intent("@ai volvé a la versión 3")
    assert intent.target == vt.RestoreTarget.NUMBER and intent.version_number == 3


def test_history_answer_uses_diff_against_current():
    svc = FakeVersionService([make_version(2), make_version(1)])
    svc.diff_changes = [DiagramChange(kind="class_added", target_id="c", description="Se agregó la clase Producto")]
    reply = vt.answer_history(svc, "d")
    assert "v2" in reply and "Se agregó la clase Producto" in reply


def test_history_answer_without_versions():
    assert "Todavía no hay versiones" in vt.answer_history(FakeVersionService(), "d")


def test_ai_checkpoint_is_authored_by_ai():
    svc = FakeVersionService()
    res = vt.create_ai_checkpoint(svc, DOC, "Ana")
    assert "checkpoint v1" in res.reply
    assert svc.snapshots[0]["kind"] == VersionKind.CHECKPOINT
    assert svc.snapshots[0]["author_type"] == VersionAuthorType.AI


def test_restore_is_only_ever_proposed():
    svc = FakeVersionService([make_version(5), make_version(4)])
    res = vt.build_restore_operation(svc, "d", vt.detect_version_intent("volvé a la versión 4"))
    assert len(res.operations) == 1
    assert res.operations[0]["op"] == "RESTORE_VERSION" and res.operations[0]["version_number"] == 4
    assert svc.snapshots == []  # nothing was executed


def test_undo_last_ai_targets_latest_pre_ai_version():
    svc = FakeVersionService(
        [make_version(6), make_version(5, kind=VersionKind.PRE_AI), make_version(3, kind=VersionKind.PRE_AI)]
    )
    res = vt.build_restore_operation(svc, "d", vt.detect_version_intent("deshacé lo último que hiciste"))
    assert res.operations[0]["version_number"] == 5


def test_restore_unknown_version_gives_no_operation():
    svc = FakeVersionService([make_version(1)])
    res = vt.build_restore_operation(svc, "d", vt.detect_version_intent("volvé a la versión 9"))
    assert res.operations == [] and "9" in res.reply


def test_pre_ai_snapshot_failure_returns_none():
    assert vt.create_pre_ai_snapshot(FakeVersionService(fail_snapshot=True), DOC, "x") is None


K = vt.VersionIntentKind


@pytest.mark.parametrize(
    "text",
    [
        "crea la clase Historial",
        "hacé un hotel con historial de reservas",
        "agrega la tabla historial clinico",
        "guarda la version del libro como atributo",
        "@ai creá la clase Version con numero",
        "agregá el campo historial a Paciente",
        "mostrame el historial clinico del paciente",
        "armá la base de datos de una biblioteca con historial de prestamos",
    ],
)
def test_domain_prompts_are_not_hijacked_by_version_intent(text):
    assert vt.detect_version_intent(text) is None


@pytest.mark.parametrize(
    "text,kind",
    [
        ("guardá una versión", K.CHECKPOINT),
        ("guardá un checkpoint", K.CHECKPOINT),
        ("hacé un checkpoint", K.CHECKPOINT),
        ("punto de restauración", K.CHECKPOINT),
        ("@ai crea un snapshot de la clase Cliente", K.CHECKPOINT),
        ("qué cambió en el diagrama", K.HISTORY),
        ("historial de versiones", K.HISTORY),
        ("mostrame las versiones", K.HISTORY),
        ("mostrame el historial de cambios del diagrama", K.HISTORY),
        ("volvé a la versión 3", K.RESTORE),
        ("volvé a la versión 3 de la tabla Cliente", K.RESTORE),
        ("deshacé lo último que hizo la IA", K.RESTORE),
    ],
)
def test_real_version_requests_still_work(text, kind):
    intent = vt.detect_version_intent(text)
    assert intent is not None and intent.kind == kind
