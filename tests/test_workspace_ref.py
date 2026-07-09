from yuubot.domain.messages import ContentItem
from yuubot.web.workspace_ref import normalize_conversation_content, workspace_ref


def test_workspace_ref_formats_trimmed_path() -> None:
    assert workspace_ref(" uploads/image-jpeg/cat.jpg ") == "[[ uploads/image-jpeg/cat.jpg ]]"


def test_normalize_conversation_content_folds_text_and_files_in_order() -> None:
    assert normalize_conversation_content(
        [
            ContentItem("file", path="uploads/image-jpeg/one.jpg", mime="image/jpeg"),
            ContentItem("text", " cc "),
            ContentItem("image", path="uploads/image-jpeg/two.jpg", mime="image/jpeg"),
        ]
    ) == [
        ContentItem(
            "text",
            "[[ uploads/image-jpeg/one.jpg ]] cc [[ uploads/image-jpeg/two.jpg ]]",
        )
    ]


def test_normalize_conversation_content_drops_empty_items() -> None:
    assert normalize_conversation_content([ContentItem("file"), ContentItem("text")]) == []
