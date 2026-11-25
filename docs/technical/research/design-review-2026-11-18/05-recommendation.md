# Recommendation detail

The official PLXE Series C manual answers the semantic question:

- **User Timer / Home All Day / Out All Day:** stored daily schedules. Selecting one does not require a countdown in Home Assistant; the device already owns the schedule. The user experience should expose selection, not fabricate a duration picker.
- **Away:** a temporary override that explicitly requires a return date, hours, and target temperature. A plain climate preset is insufficient unless the return deadline is already visible and configured.
- **Current defect:** the integration silently chooses a 24-hour expiry when Away is selected without a future device expiry. This is the highest-priority UX problem.

Recommended implementation direction: a native `datetime` entity for “Away return time”, coupled with an explicit Away preset activation that refuses activation when no valid future deadline exists. If Home Assistant's native climate UI cannot collect the deadline in one interaction, the entity should make the required sequence obvious and documented rather than silently guessing.
