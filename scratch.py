import discord.ui
print([x for x in dir(discord.ui) if "File" in x or "Attachment" in x or "Input" in x])
