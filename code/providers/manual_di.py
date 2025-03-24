from abc import ABC, abstractmethod


# Use an interface to define contract for clients
# This allows us injecting different implementations
class IClient(ABC):
    @abstractmethod
    def request(self, url: str) -> str:
        pass


# Regular implementation
class RealClient(IClient):
    def request(self, url: str) -> str:
        # Some HTTP requesting logic
        return f'"{url}" call result'


# Implementation for tests
class MockClient(IClient):
    def __init__(self, return_data: str) -> None:
        self._return_data = return_data

    def request(self, url: str) -> str:
        # Mocked behavior for testing
        return f'{self._return_data} from "{url}"'


class Service:
    # Accepts any IClient implementation
    def __init__(self, client: IClient) -> None:
        self._client = client

    def do_something(self) -> str:
        return self._client.request('https://example.com')


# Usage in regular code
real_client = RealClient()
service = Service(real_client)
print(service.do_something())  # Output: "https://example.com" call result

# Usage in tests
mocked_client = MockClient('mocked data')
service = Service(mocked_client)
print(service.do_something())  # Output: mocked data from "https://example.com"
