"""Channel-distribution adapter layer.

Lets an approved "engagement" be autonomously distributed to a channel
(website, facebook, instagram, linkedin, youtube, twitter_x) and recorded as
a Publication. See base.py for the Engagement dict shape and the
ChannelAdapter contract.

A post reaches a channel one of three ways, decided per organization by
registry.get_adapter(): directly through credentials the org authorized itself
(live.py), relayed through the org's Postiz workspace (postiz.py), or simulated
(social.py) when it has authorized neither.
"""

from .base import ChannelAdapter, DISTRIBUTABLE_CHANNELS
from .website import WebsiteAdapter
from .social import SimulatedSocialAdapter
from .postiz import POSTIZ_ROUTABLE_CHANNELS, PostizAdapter, PostizClient, PostizError
from .registry import (
    distribute_engagement,
    get_adapter,
    register_adapter,
    unregister_adapter,
)

__all__ = [
    "ChannelAdapter",
    "WebsiteAdapter",
    "SimulatedSocialAdapter",
    "PostizAdapter",
    "PostizClient",
    "PostizError",
    "get_adapter",
    "register_adapter",
    "unregister_adapter",
    "distribute_engagement",
    "DISTRIBUTABLE_CHANNELS",
    "POSTIZ_ROUTABLE_CHANNELS",
]
