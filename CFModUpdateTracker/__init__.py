from .cfmod import CFModTracker


async def setup(bot):
    cog = CFModTracker(bot)
    await bot.add_cog(cog)
