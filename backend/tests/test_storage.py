import uuid

from app.services.storage_service import save_attachment, ATTACHMENT_DIR


def test_save_attachment_uses_uuid_path():
    content = b"hello"
    path = save_attachment(str(uuid.uuid4()), "evil.txt", content)
    assert path.endswith(str(ATTACHMENT_DIR)) is False
    assert "evil.txt" not in path
    assert path.startswith(str(ATTACHMENT_DIR))
