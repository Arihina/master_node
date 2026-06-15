RULES = {
    "закуп": "epoz",
    "тендер": "epoz",
    "223-фз": "epoz",

    "навиер": "cfd",
    "турбулент": "cfd",
    "газодинами": "cfd",

    "договор": "lawyer",
    "иск": "lawyer",
    "суд": "lawyer",
}


def route(message: str) -> str | None:
    text = message.lower()

    for keyword, agent in RULES.items():
        if keyword in text:
            return agent

    return None
