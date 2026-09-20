from typing import Any, Callable, Dict
from app.application.command_handlers.uml_handlers import (
    handle_add_attribute,
    handle_create_class,
    handle_create_relationship,
    handle_delete_attribute,
    handle_delete_class,
    handle_delete_relationship,
    handle_move_class,
    handle_rename_class,
    handle_update_attribute,
    handle_update_relationship,
)
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.commands import (
    AddAttributeCommand,
    CommandTypeEnum,
    CreateClassCommand,
    CreateRelationshipCommand,
    DeleteAttributeCommand,
    DeleteClassCommand,
    DeleteRelationshipCommand,
    MoveClassCommand,
    RenameClassCommand,
    UmlCommand,
    UpdateAttributeCommand,
    UpdateRelationshipCommand,
)


class CommandBus:
    """Dispatches strongly-typed UML commands to their respective domain handlers."""

    def __init__(self):
        self._handlers: Dict[CommandTypeEnum, Callable[[CanonicalUmlDocument, Any], CanonicalUmlDocument]] = {
            CommandTypeEnum.CREATE_CLASS: handle_create_class,
            CommandTypeEnum.RENAME_CLASS: handle_rename_class,
            CommandTypeEnum.DELETE_CLASS: handle_delete_class,
            CommandTypeEnum.MOVE_CLASS: handle_move_class,
            CommandTypeEnum.ADD_ATTRIBUTE: handle_add_attribute,
            CommandTypeEnum.UPDATE_ATTRIBUTE: handle_update_attribute,
            CommandTypeEnum.DELETE_ATTRIBUTE: handle_delete_attribute,
            CommandTypeEnum.CREATE_RELATIONSHIP: handle_create_relationship,
            CommandTypeEnum.UPDATE_RELATIONSHIP: handle_update_relationship,
            CommandTypeEnum.DELETE_RELATIONSHIP: handle_delete_relationship,
        }

    def dispatch(self, document: CanonicalUmlDocument, command: UmlCommand) -> CanonicalUmlDocument:
        handler = self._handlers.get(command.command_type)
        if not handler:
            raise ValueError(f"No handler registered for command type: {command.command_type}")
        return handler(document, command)


# Global singleton instance
command_bus = CommandBus()

