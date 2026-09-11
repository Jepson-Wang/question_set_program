
async def test_fixture_is_isolated(redis_test_client):
    await redis_test_client.set("k","v")
    assert await redis_test_client.get("k") == "v"

async def test_asyncio_auto_mode_works():
    import asyncio
    await asyncio.sleep(0)
    assert True