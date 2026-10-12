"""Public service catalogue. No router-specific configuration lives here."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Service:
    name: str
    provider: str
    selector: str
    owner: str = ""


CATALOG = {
    "cod": Service("Call of Duty / Demonware", "ripe", "60229", "DEMONWARE"),
    "blizzard": Service("Blizzard / Battle.net", "ripe", "57976", "BLIZZARD"),
    "riot": Service("Riot Games", "ripe", "6507", "RIOT"),
    "pubg": Service("PUBG / Krafton", "ripe", "18321", "KRAFTON"),
    "destiny": Service("Destiny / Bungie", "ripe", "394073", "BUNGIE"),
    "i3d": Service("i3D.net game hosting", "ripe", "49544", "I3DNET"),
    "fortnite": Service("Fortnite (AWS Frankfurt + Epic anchors)", "aws_fortnite", "eu-central-1:EC2"),
    "m365": Service("Microsoft 365 (all service areas)", "m365", "all"),
    "teams": Service("Microsoft Teams / Skype + Common", "m365", "Skype"),
    "exchange": Service("Exchange Online + Common", "m365", "Exchange"),
    "sharepoint": Service("SharePoint / OneDrive + Common", "m365", "SharePoint"),
    "azure": Service("Azure (selected service tags)", "azure", "selected"),
    "aws": Service("AWS (selected regions and services)", "aws", "selected"),
    "sap": Service("SAP (your documented destination prefixes)", "manual", "selected"),
    "custom": Service("Custom destination prefixes", "manual", "selected"),
}
