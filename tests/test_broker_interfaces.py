"""Protocol compliance tests for execution/interfaces.py (IBroker).

No MT5 terminal connection is made -- MT5Broker() only constructs an
unconnected MT5Connector, it does not call mt5.initialize()/login().
PaperBroker() reads/writes its local JSON state file but makes no MT5 calls
either (it only calls MT5Connector.fetch_recent_bars() lazily, when an
order/position operation actually needs a price).
"""

from pathlib import Path

import pytest

import execution.paper_broker as paper_broker_module
from execution.interfaces import IBroker
from execution.mt5_broker import MT5Broker
from execution.paper_broker import PaperBroker


class TestIBrokerProtocolCompliance:
    """Structural (duck-typed) compliance checks for MT5Broker/PaperBroker against IBroker."""

    def test_mt5_broker_satisfies_ibroker(self) -> None:
        broker = MT5Broker()
        assert isinstance(broker, IBroker)

    def test_mt5_broker_class_satisfies_ibroker(self) -> None:
        """isinstance() against a runtime_checkable Protocol only checks method presence.

        This also holds at the class level, without instantiation.
        """
        assert issubclass(MT5Broker, IBroker)

    def test_paper_broker_satisfies_ibroker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(paper_broker_module, "STATE_FILE", tmp_path / "paper_broker_state.json")
        broker = PaperBroker()
        assert isinstance(broker, IBroker)

    def test_paper_broker_class_satisfies_ibroker(self) -> None:
        assert issubclass(PaperBroker, IBroker)

    def test_object_missing_broker_methods_does_not_satisfy_ibroker(self) -> None:
        class NotABroker:
            pass

        assert not isinstance(NotABroker(), IBroker)

    def test_partial_implementation_does_not_satisfy_ibroker(self) -> None:
        class PartialBroker:
            def connect(self) -> bool:
                return True

        assert not isinstance(PartialBroker(), IBroker)

    def test_ibroker_declares_all_required_methods(self) -> None:
        required = {
            "connect",
            "place_order",
            "cancel_order",
            "close_position",
            "get_open_positions",
            "get_account_info",
        }
        assert required.issubset(dir(IBroker))
