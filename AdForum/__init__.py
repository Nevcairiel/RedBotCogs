import asyncio
from .adforum import AdForum


async def setup(bot):
    cog = AdForum(bot)
    await bot.add_cog(cog)
    asyncio.create_task(cog.initialize())
