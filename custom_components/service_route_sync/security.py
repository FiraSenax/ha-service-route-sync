"""Authorization shared by entry points that can change network policy."""

from homeassistant.exceptions import Unauthorized


async def require_admin(hass, context):
    """Permit administrators and trusted HA automation contexts, never ordinary users.

    The HA service layer supplies the context; clients cannot select another user
    or turn an authenticated service call into a trusted system invocation.
    """
    if context.user_id is None:
        return
    user = await hass.auth.async_get_user(context.user_id)
    if user is None or not user.is_admin:
        raise Unauthorized(context=context)
