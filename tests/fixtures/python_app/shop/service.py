from . import pricing as prices
from .pricing import total as calculate


def quote(value: int) -> int:
    return calculate(value)


async def quote_async(value: int) -> int:
    return prices.total(value)


def dispatch(handler, value):
    return handler(value)


def nested(value):
    def helper():
        return calculate(value)

    return helper()


class Service:
    def run(self, value):
        return self.quote(value)
