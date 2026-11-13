"""Built-in Step 2 MCP connectors for Discord, Mail, Google Workspace, X, and YouTube."""

from app.mcp_hub.servers.discord_mcp import DiscordMCPConnector
from app.mcp_hub.servers.google_mcp import GoogleWorkspaceMCPConnector
from app.mcp_hub.servers.mail_mcp import MailMCPConnector
from app.mcp_hub.servers.social_mcp import SocialMediaMCPConnector

__all__ = [
    "DiscordMCPConnector",
    "GoogleWorkspaceMCPConnector",
    "MailMCPConnector",
    "SocialMediaMCPConnector",
]

