from backend.dao.user_profile_mapper import append_notes, merge_json_fields

def test_new_keys_are_added():
    assert merge_json_fields({"函数": "薄弱"},{"方程": "薄弱"}) == {
        "函数": "薄弱",
        "方程": "薄弱"
    }

def test_existing_key_takes_new_value():
    """测试的是如果发生冲突，优先采用后面的"""
    assert merge_json_fields({"函数": "薄弱"}, {"函数": "已掌握"}) == {"函数": "已掌握"}

def test_old_keys_are_never_dropped():
    """写入新知识点不得冲掉已有的。"""
    old = {"函数": "薄弱", "几何": "薄弱", "概率": "一般"}
    merged = merge_json_fields(old, {"方程": "薄弱"})
    assert set(merged) == {"函数", "几何", "概率", "方程"}

def test_none_and_empty_are_safe():
    assert merge_json_fields(None,{"方程": "薄弱"}) == {"方程": "薄弱"}
    assert merge_json_fields({"函数":"薄弱"},None) == {"函数":"薄弱"}
    assert merge_json_fields(None,None) == {}

def test_notes_are_append_not_replaced():
    old = ["做题时喜欢先看思路"]
    assert append_notes(old,["晚上做题效率更高"]) == [
        "做题时喜欢先看思路",
        "晚上做题效率更高",
    ]

def test_notes_skip_exact_duplicates():
    old = ["做题时喜欢先看思路"]
    assert append_notes(old, ["做题时喜欢先看思路"]) == ["做题时喜欢先看思路"]

def test_notes_are_capped_dropping_oldest():
    old = [f"观察{i}" for i in range(50)]
    got = append_notes(old, ["最新观察"], max_size=50)
    assert len(got) == 50
    assert got[-1] == "最新观察"
    assert "观察0" not in got, "超限时丢最旧的"


def test_notes_none_is_safe():
    assert append_notes(None, ["第一条"]) == ["第一条"]
    assert append_notes(["已有"], None) == ["已有"]
    assert append_notes(None, None) == []