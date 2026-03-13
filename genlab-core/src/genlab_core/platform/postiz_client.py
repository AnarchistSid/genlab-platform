"""SHIM: Re-exports from genlab_core.platforms.postiz."""
from genlab_core.platforms.postiz import *  # noqa: F401,F403
from genlab_core.platforms.postiz import (  # explicit re-exports for type checkers
    MultiPublishResult,
    PostizClient,
    PostizPlatform,
    PublishResult,
    ShadowPublisher,
)
