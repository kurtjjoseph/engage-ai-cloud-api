"""Channel-distribution adapter layer.

Lets an approved "engagement" be autonomously distributed to a channel
(website, facebook, instagram, linkedin, youtube, twitter_x) and recorded as
a Publication. See base.py for the Engagement dict shape and the
ChannelAdapter contract.
"""

from .base import ChannelAdapter, DISTRIBUTABLE_CHANNELS
from .website import WebsiteAdapter
from .social import SimulatedSocialAdapter
from .registry import get_adapter, register_adapter, distribute_engagement

__all__ = [
    "ChannelAdapter",
    "WebsiteAdapter",
    "SimulatedSocialAdapter",
    "get_adapter",
    "register_adapter",
    "distribute_engagement",
    "DISTRIBUTABLE_CHANNELS",
]
