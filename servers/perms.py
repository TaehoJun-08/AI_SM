"""What people may do inside a server. Every power is one bit, so a role stores them in one number.
The owner and anyone with the Administrator flag can do everything; everybody else gets the
server's base powers plus the powers of each role they hold."""

PERMS = [
    ("send", "Send messages", "Talk in text channels and threads."),
    ("attach", "Attach files", "Share photos, videos, audio, documents and GIFs."),
    ("react", "Add reactions", "React to messages and vote in polls."),
    ("invite", "Create invites", "Make invite links for the server."),
    ("mention_all", "Mention everyone", "Use @everyone and @here."),
    ("manage_messages", "Manage messages", "Delete anyone's message, pin messages, ignore slow-mode."),
    ("manage_channels", "Manage channels", "Create, edit and delete channels and categories."),
    ("manage_roles", "Manage roles", "Create roles and give them to people lower than you."),
    ("manage_server", "Manage server", "Change the name, rules, welcome note, goal and emoji."),
    ("manage_events", "Manage events", "Create and cancel server events."),
    ("manage_companions", "Manage companions", "Add, configure and remove bots and webhooks."),
    ("timeout", "Time out members", "Mute someone for a while."),
    ("kick", "Kick members", "Remove people (they can rejoin)."),
    ("ban", "Ban members", "Remove people for good."),
    ("view_audit", "View the audit log", "See who changed what."),
]
BIT = {key: 1 << i for i, (key, _, _) in enumerate(PERMS)}
LABEL = {key: label for key, label, _ in PERMS}
ALL = frozenset(BIT)
DEFAULT_BASE = BIT["send"] | BIT["attach"] | BIT["react"] | BIT["invite"]
# Having any of these opens Server Settings (the tabs inside show only what you can use).
SETTINGS = {"manage_server", "manage_channels", "manage_roles", "manage_companions", "manage_events",
            "timeout", "kick", "ban", "view_audit"}


def to_set(mask):
    return {key for key, bit in BIT.items() if mask & bit}


def to_mask(keys):
    return sum(BIT[k] for k in keys if k in BIT)
