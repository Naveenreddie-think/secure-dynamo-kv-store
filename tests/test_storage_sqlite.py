from dynamokv.storage.sqlite import SqliteStorage


def test_data_survives_reopening_the_database(tmp_path):
    db_path = str(tmp_path / "restart.db")

    first = SqliteStorage(db_path)
    first.put("foo", {"nested": "value"})

    second = SqliteStorage(db_path)
    assert second.get("foo") == {"nested": "value"}
