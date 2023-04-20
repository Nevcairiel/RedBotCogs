from .steamwhitelist import SteamWhitelist

def setup(bot):
    bot.add_cog(SteamWhitelist(bot))
