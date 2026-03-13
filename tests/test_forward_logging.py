from yuubot.recorder.server import _render_forward_log_lines


def test_render_forward_log_lines_expands_nested_forward_up_to_three_levels():
    nodes = [
        {
            "user_id": 1,
            "nickname": "Alice",
            "content": "第一层",
            "children": [
                {
                    "user_id": 2,
                    "nickname": "Bob",
                    "content": "第二层",
                    "children": [
                        {
                            "user_id": 3,
                            "nickname": "Carol",
                            "content": "第三层",
                            "children": [
                                {
                                    "user_id": 4,
                                    "nickname": "Dave",
                                    "content": "第四层",
                                    "children": [],
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    ]

    lines = _render_forward_log_lines("fw-1", nodes, max_depth=3)

    assert any("第一层" in line for line in lines)
    assert any("第二层" in line for line in lines)
    assert any("第三层" in line for line in lines)
    assert not any("第四层" in line for line in lines)
