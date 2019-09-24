"""
Author: Wudi
bitstamp合约接口
"""

import hashlib
import hmac
import sys
import time
import re
import uuid
from copy import copy
from datetime import datetime, timedelta
from urllib.parse import urlencode
from typing import Dict, Set

from vnpy.api.rest import Request, RestClient
from vnpy.api.websocket import WebsocketClient

from vnpy.trader.constant import (
    Direction,
    Exchange,
    OrderType,
    Product,
    Status,
    Interval
)
from vnpy.trader.gateway import BaseGateway, LocalOrderManager
from vnpy.trader.object import (
    TickData,
    OrderData,
    TradeData,
    BarData,
    PositionData,
    AccountData,
    ContractData,
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    HistoryRequest
)

from vnpy.trader.event import EVENT_TIMER


REST_HOST = "https://www.bitstamp.net/api/v2"
WEBSOCKET_HOST = "wss://ws.bitstamp.net"

STATUS_BITSTAMP2VT = {
    "ACTIVE": Status.NOTTRADED,
    "PARTIALLY FILLED": Status.PARTTRADED,
    "EXECUTED": Status.ALLTRADED,
    "CANCELED": Status.CANCELLED,
}

ORDERTYPE_VT2BITSTAMP = {
    OrderType.LIMIT: "EXCHANGE LIMIT",
    OrderType.MARKET: "EXCHANGE MARKET",
}

DIRECTION_VT2BITSTAMP = {
    Direction.LONG: "Buy",
    Direction.SHORT: "Sell",
}

DIRECTION_BITSTAMP2VT = {
    "0": Direction.LONG,
    "1": Direction.SHORT,
}

INTERVAL_VT2BITSTAMP = {
    Interval.MINUTE: "60",
    Interval.HOUR: "3600",
    Interval.DAILY: "86400",
}

TIMEDELTA_MAP = {
    Interval.MINUTE: timedelta(minutes=1),
    Interval.HOUR: timedelta(hours=1),
    Interval.DAILY: timedelta(days=1),
}


symbol_name_map = {}


class BitstampGateway(BaseGateway):
    """
    VN Trader Gateway for BITSTAMP connection.
    """

    default_setting = {
        "key": "",
        "secret": "",
        "username": "",
        "session": 3,
        "proxy_host": "127.0.0.1",
        "proxy_port": 1080,
    }

    exchanges = [Exchange.BITSTAMP]

    def __init__(self, event_engine):
        """Constructor"""
        super().__init__(event_engine, "BITSTAMP")

        self.order_manager = LocalOrderManager(self)

        self.rest_api = BitstampRestApi(self)
        self.ws_api = BitstampWebsocketApi(self)

    def connect(self, setting: dict):
        """"""
        key = setting["key"]
        secret = setting["secret"]
        username = setting["username"]
        session = setting["session"]
        proxy_host = setting["proxy_host"]
        proxy_port = setting["proxy_port"]

        self.rest_api.connect(key, secret, username,
                              session, proxy_host, proxy_port)
        self.ws_api.connect(proxy_host, proxy_port)

    def subscribe(self, req: SubscribeRequest):
        """"""
        self.ws_api.subscribe(req)

    def send_order(self, req: OrderRequest):
        """"""
        return self.rest_api.send_order(req)

    def cancel_order(self, req: CancelRequest):
        """"""
        self.rest_api.cancel_order(req)

    def query_account(self):
        """"""
        pass

    def query_position(self):
        """"""
        pass

    def query_history(self, req: HistoryRequest):
        """"""
        return self.rest_api.query_history(req)

    def close(self):
        """"""
        self.rest_api.stop()
        self.ws_api.stop()


class BitstampRestApi(RestClient):
    """
    Bitstamp REST API
    """

    def __init__(self, gateway: BaseGateway):
        """"""
        super(BitstampRestApi, self).__init__()

        self.gateway = gateway
        self.gateway_name = gateway.gateway_name
        self.order_manager = gateway.order_manager

        self.key = ""
        self.secret = ""
        self.username = "qxfe9863"

        self.order_count = 1_000_000
        self.connect_time = 0

    def connect(
        self,
        key: str,
        secret: str,
        username: str,
        session: int,
        proxy_host: str,
        proxy_port: int,
    ):
        """
        Initialize connection to REST server.
        """
        self.key = key
        self.secret = secret.encode()
        self.username = username

        self.connect_time = (
            int(datetime.now().strftime("%y%m%d%H%M%S")) * self.order_count
        )

        self.init(REST_HOST, proxy_host, proxy_port)
        self.start(session)

        self.gateway.write_log("REST API启动成功")

        self.query_contract()
        self.query_account()
        self.query_order()

    def sign(self, request: Request):
        """
        Sign Bitstamp request.
        """
        if request.method == "GET":
            return request

        timestamp = str(int(round(time.time() * 1000)))
        nonce = str(uuid.uuid4())
        content_type = "application/x-www-form-urlencoded"

        # Empty post data leads to API0020 error,
        # so use this offset dict instead.
        if not request.data:
            request.data = {"offset": "1"}
        payload_str = urlencode(request.data)

        message = "BITSTAMP " + self.key + \
            request.method + \
            "www.bitstamp.net/api/v2" + \
            request.path + \
            "" + \
            content_type + \
            nonce + \
            timestamp + \
            "v2" + \
            payload_str
        message = message.encode("utf-8")

        signature = hmac.new(
            self.secret,
            msg=message,
            digestmod=hashlib.sha256
        ).hexdigest().upper()

        request.headers = {
            "X-Auth": "BITSTAMP " + self.key,
            "X-Auth-Signature": signature,
            "X-Auth-Nonce": nonce,
            "X-Auth-Timestamp": timestamp,
            "X-Auth-Version": "v2",
            "Content-Type": content_type
        }
        print(payload_str)
        request.data = payload_str

        return request

    def query_order(self):
        """"""
        path = "/open_orders/all/"

        self.add_request(
            method="POST",
            path=path,
            callback=self.on_query_order
        )

    def on_query_order(self, data, request):
        """获取委托订单"""
        for d in data:
            sys_orderid = d["id"]
            local_orderid = self.order_manager.get_local_orderid(sys_orderid)

            direction = DIRECTION_BITSTAMP2VT[d["type"]]

            order = OrderData(
                orderid=local_orderid,
                symbol=d["currency_pair"],
                exchange=Exchange.BITSTAMP,
                price=float(d["price"]),
                volume=float(d["amount"]),
                traded=float(0),
                direction=direction,
                time=d["datetime"],
                gateway_name=self.gateway_name,
            )

            self.order_manager.on_order(order)

        self.gateway.write_log("委托信息查询成功")

    def query_account(self):
        """"""
        path = "/balance/"

        self.add_request(
            method="POST",
            path=path,
            callback=self.on_query_account
        )

    def on_query_account(self, data, request):
        """"""
        for key in data.keys():
            if "balance" not in key:
                continue
            currency = key.replace("_balance", "")

            account = AccountData(
                accountid=currency,
                balance=float(data[currency + "_balance"]),
                frozen=float(data[currency + "_reserved"]),
                gateway_name=self.gateway_name
            )
            self.gateway.on_account(account)

    def query_contract(self):
        """"""
        self.add_request(
            method="GET",
            path="/trading-pairs-info/",
            callback=self.on_query_contract,
        )

    def on_query_contract(self, data, request):
        """"""
        for d in data:
            pricetick = 1 / pow(10, d["counter_decimals"])
            min_volume = 1 / pow(10, d["base_decimals"])

            contract = ContractData(
                symbol=d["url_symbol"],
                exchange=Exchange.BITSTAMP,
                name=d["name"],
                product=Product.SPOT,
                size=1,
                pricetick=pricetick,
                min_volume=min_volume,
                history_data=True,
                gateway_name=self.gateway_name,
            )
            self.gateway.on_contract(contract)

            symbol_name_map[contract.symbol] = contract.name

        self.gateway.write_log("合约信息查询成功")

    def cancel_order(self, req: CancelRequest):
        """"""
        path = "/cancel_order/"

        sys_orderid = self.order_manager.get_sys_orderid(req.orderid)

        data = {"id": sys_orderid}

        self.add_request(
            method="POST",
            path=path,
            data=data,
            callback=self.on_cancel_order,
            extra=req
        )

    def on_cancel_order(self, data, request):
        """"""
        cancel_request = request.extra
        local_orderid = cancel_request.orderid
        order = self.order_manager.get_order_with_local_orderid(local_orderid)

        if "error" in data:
            order.status = Status.REJECTED
        else:
            order.status = Status.CANCELLED

            self.gateway.write_log(f"委托撤单成功：{order.orderid}")

        self.order_manager.on_order(order)

    def on_cancel_order_error(self, data, request):
        """"""
        error_msg = data["error"]
        self.gateway.write_log(f"撤单请求出错，信息：{error_msg}")

    def send_order(self, req: OrderRequest):
        """"""
        local_orderid = self.order_manager.new_local_orderid()
        order = req.create_order_data(
            local_orderid,
            self.gateway_name
        )
        order.time = datetime.now().strftime("%H:%M:%S")

        data = {
            "amount": req.volume,
            "price": req.price
        }

        if req.direction == Direction.LONG:
            if req.type == OrderType.LIMIT:
                path = f"/buy/{req.symbol}/"
            elif req.type == OrderType.MARKET:
                path = f"/buy/market/{req.symbol}/"
        else:
            if req.type == OrderType.LIMIT:
                path = f"/sell/{req.symbol}/"
            elif req.type == OrderType.MARKET:
                path = f"/sell/market/{req.symbol}/"

        self.add_request(
            method="POST",
            path=path,
            data=data,
            callback=self.on_send_order,
            extra=order,
        )
        self.order_manager.on_order(order)
        return order.vt_orderid

    def on_send_order(self, data, request):
        """"""
        print("on_send", data)
        order = request.extra

        if ["reason"] in data:
            order.status = Status.REJECTED
            self.order_manager.on_order(order)
            return

        sys_orderid = data["id"]
        self.order_manager.update_orderid_map(order.orderid, sys_orderid)

    def on_send_order_error(
        self, exception_type: type, exception_value: Exception, tb, request: Request
    ):
        """
        Callback when sending order caused exception.
        """
        # Record exception if not ConnectionError
        if not issubclass(exception_type, ConnectionError):
            self.on_error(exception_type, exception_value, tb, request)

    def on_failed(self, status_code: int, request: Request):
        """
        Callback to handle request failed.
        """
        data = request.response.json()
        reason = data["reason"]
        code = data["code"]

        msg = f"{request.path} 请求失败，状态码：{status_code}，信息： {reason} code: {code}"
        self.gateway.write_log(msg)

    def on_error(
        self, exception_type: type, exception_value: Exception, tb, request: Request
    ):
        """
        Callback to handler request exception.
        """
        msg = f"触发异常，状态码：{exception_type}，信息：{exception_value}"
        self.gateway.write_log(msg)

        sys.stderr.write(
            self.exception_detail(exception_type, exception_value, tb, request)
        )


class BitstampWebsocketApi(WebsocketClient):
    """"""

    def __init__(self, gateway):
        """"""
        super().__init__()

        self.gateway = gateway
        self.gateway_name = gateway.gateway_name
        self.order_manager = gateway.order_manager

        self.subscribed: Dict[str, SubscribeRequest] = {}
        self.ticks: Dict[str, TickData] = {}

    def connect(self, proxy_host: str, proxy_port: int):
        """"""
        self.init(WEBSOCKET_HOST, proxy_host, proxy_port)
        self.start()

    def on_connected(self):
        """"""
        self.gateway.write_log("Websocket API连接成功")

        # Auto re-subscribe market data after reconnected
        for req in self.subscribed.values():
            self.subscribe(req)

    def subscribe(self, req: SubscribeRequest):
        """"""
        self.subscribed[req.symbol] = req
        if not self._active:
            return

        tick = TickData(
            symbol=req.symbol,
            name=symbol_name_map.get(req.symbol, ""),
            exchange=Exchange.BITSTAMP,
            datetime=datetime.now(),
            gateway_name=self.gateway_name,
        )

        for prefix in [
            "order_book_",
            "live_trades_",
            "live_orders_"
        ]:
            channel = f"{prefix}{req.symbol}"
            d = {
                "event": "bts:subscribe",
                "data": {
                    "channel": channel
                }
            }
            self.ticks[channel] = tick
            self.send_packet(d)

    def on_packet(self, packet):
        """"""
        event = packet["event"]

        if event == "trade":
            self.on_market_trade(packet)
        elif event == "data":
            self.on_market_depth(packet)
        elif "order_" in event:
            self.on_market_order(packet)
        elif event == "bts:request_reconnect":
            self._disconnect()      # Server requires to reconnect

    def on_market_trade(self, packet):
        """"""
        channel = packet["channel"]
        data = packet["data"]

        tick = self.ticks[channel]
        tick.last_price = data["price"]
        tick.last_volume = data["amount"]
        tick.datetime = datetime.fromtimestamp(int(data["timestamp"]))

        self.gateway.on_tick(copy(tick))

        buy_orderid = data["buy_order_id"]
        sell_orderid = data["sell_order_id"]

        for sys_orderid in [buy_orderid, sell_orderid]:
            order = self.order_manager.get_order_with_sys_orderid(sys_orderid)

            if order:
                order.traded += data["amount"]

                if order.traded < order.volume:
                    order.status = Status.PARTTRADED
                else:
                    order.status = Status.ALLTRADED

                self.order_manager.on_order(copy(order))

    def on_market_depth(self, packet):
        """"""
        channel = packet["channel"]
        data = packet["data"]

        tick = self.ticks[channel]
        tick.datetime = datetime.fromtimestamp(int(data["timestamp"]))

        bids = data["bids"]
        asks = data["asks"]

        for n in range(5):
            ix = n + 1

            bid_price, bid_volume = bids[n]
            tick.__setattr__(f"bid_price_{ix}", float(bid_price))
            tick.__setattr__(f"bid_volume_{ix}", float(bid_volume))

            ask_price, ask_volume = asks[n]
            tick.__setattr__(f"ask_price_{ix}", float(ask_price))
            tick.__setattr__(f"ask_volume_{ix}", float(ask_volume))

        self.gateway.on_tick(copy(tick))

    def on_market_order(self, packet):
        """"""
        event = packet["event"]
        data = packet["data"]

        if event != "order_deleted":
            return

        sys_orderid = data["id"]
        order = self.order_manager.get_order_with_sys_orderid(sys_orderid)
        if order:
            order.status = Status.CANCELLED
            self.order_manager.on_order(copy(order))