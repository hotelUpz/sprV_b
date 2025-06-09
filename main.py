from api import get_dex_prices, get_mexc_prices, TelegramNotifier
from utils import Utils
import asyncio
import aiohttp
from typing import Optional, Tuple, List, Dict
import traceback
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANEL_ID = os.getenv("CHANEL_ID")

# Settings:
# ///////////
ADDRESSES_DATA = {
    "TIBBIR_USDT": ('base', '0x0c3b466104545efa096b8f944c1e524e1d0d4888'),
    # "ZERO_USDT": ('linea', '0x0040f36784dda0821e74ba67f86e084d70d67a3a'),
    "JAGER_USDT": ('bsc', '0x589e1c953bcb822a2094fd8c7cbbd84a7762fb04'),
    "BUBB_USDT": ('bsc','0xc8255e3fa0f4c6e6678807d663f9e2263e23a8e8'),
}
SYMBOLS = ["TIBBIR_USDT", "BUBB_USDT"]

# Timing:
interval_map = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "30m": 1800,
}

DATA_REFRESH_INTERVAL = interval_map["5m"]
TEXT_REFRESH_INTERVAL = interval_map["5m"]
PRICE_REFRESH_INTERVAL = int(len(SYMBOLS)* 2.5)

# Strayegy:
WINDOW = 576 # minute
HIST_SPREAD_LIMIT = 600
DIRECTION_MODE = 3 # 1 -- Long only, 2 -- Short only, 3 -- Long + Short:
DEVIATION = 0.89 # hvh
FIXED_THRESHOLD = {
    "TIBBIR_USDT": {
        "is_active": True,
        "long_val": -3.0, # %
        "short_val": 3.0 # %
    },
    "BUBB_USDT": {
        "is_active": True,
        "long_val": -3.0, # %
        "short_val": 3.0 # %
    },
    "JAGER_USDT": {
        "is_active": True,
        "long_val": -3.5, # %
        "short_val": 4.0 # %
    },
}
EXIT_THRESHOLD = 0.5
CALC_SPREAD_METHOD = 'a'

# Utils:
PLOT_WINDOW = 576 # minute
MAX_RECONNECT_ATTEMPTS = 21

        
class NetworkServices():
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def _check_session_connection(self, session):
        try:
            async with session.get("https://api.mexc.com/api/v3/ping") as response:
                return response.status == 200
        except aiohttp.ClientError:
            return False

    async def validate_session(self):
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            if self.session and not self.session.closed:
                if await self._check_session_connection(self.session):
                    return True
                try:
                    await self.session.close()
                except Exception as e:
                    print(f"Ошибка при закрытии сессии: {e}")

            await asyncio.sleep((attempt * 1.6) + 1)
            print(f"🔁 Попытка восстановить сессию ({attempt}/{MAX_RECONNECT_ATTEMPTS})...")
            await self.initialize_session()

        print("❌ Не удалось восстановить сессию после нескольких попыток.", True)
        return False

    async def shutdown_session(self):
        """Закрытие aiohttp-сессии при остановке."""
        if self.session and not self.session.closed:
            try:
                await self.session.close()
            except Exception as e:
                print(f"Ошибка при закрытии сессии в shutdown_session(): {e}")
    
class SignalProcessor:
    @staticmethod
    def hvh_spread_calc(symbol, spread_pct_data, last_spread):
        """
        Простой HVH-индикатор:
        - spread_pct_data: list of tuples (timestamp, spread_value, optional_extra)
        - last_spread: последнее значение спреда
        Returns: 1 (long), -1 (short), 0 (нейтрально)
        """
        long_val = FIXED_THRESHOLD[symbol]["long_val"] # negative val
        short_val = FIXED_THRESHOLD[symbol]["short_val"] # positive val
        if not FIXED_THRESHOLD[symbol]["is_active"] and len(spread_pct_data) >= WINDOW:
            recent = spread_pct_data[-WINDOW:]
            values = {
                x[i]
                for x in recent
                for i in (0, 1, 2)
                if x and len(x) > i and isinstance(x[i], (int, float))
            }
            positives = [v for v in values if v > 0]
            negatives = [v for v in values if v < 0]

            highest_level = max(positives) * DEVIATION if positives else short_val
            lowest_level = min(negatives) * DEVIATION if negatives else long_val
        else:            
            highest_level, lowest_level = short_val, long_val

        if last_spread < lowest_level:
            return 1
        if last_spread > highest_level:
            return -1
        return 0
        
    @staticmethod
    def is_exit_signal(current_spread: float, position_side: str) -> bool:
        return {
            "LONG": current_spread > -EXIT_THRESHOLD,
            "SHORT": current_spread < EXIT_THRESHOLD
        }.get(position_side, False)

    def signals_collector(
        self,
        symbol: str,
        spread_data: list,
        current_spread: float,
        in_position_long: bool,
        in_position_short: bool
    ) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], bool, bool]:
        """
        Возвращает два списка:
        - instructions_open: список сигналов на открытие
        - instructions_close: список сигналов на закрытие
        """
        instructions_open = []
        instructions_close = []
        
        if in_position_long:
            if self.is_exit_signal(current_spread, "LONG"):
                instructions_close.append(("LONG", "is_closing"))
                in_position_long = False
        if in_position_short:
            if self.is_exit_signal(current_spread, "SHORT"):
                instructions_close.append(("SHORT", "is_closing"))
                in_position_short = False

        signal = self.hvh_spread_calc(symbol, spread_data, current_spread)
        if signal == 1 and not in_position_long:
            instructions_open.append(("LONG", "is_opening"))
            in_position_long = True
        elif signal == -1 and not in_position_short:
            instructions_open.append(("SHORT", "is_opening"))
            in_position_short = True

        return instructions_open, instructions_close, in_position_long, in_position_short

class DataFetcher:
    def __init__(self):
        self.utils = Utils(PLOT_WINDOW)
        self.signals = SignalProcessor()
        self.temporary_tik_data = {}
        self.data = {}
        self._init_symbol_data()
        self.pairs: List[Tuple] = self.get_dex_pairs(self.data)

    def _init_symbol_data(self):
        for symbol in SYMBOLS:
            self.temporary_tik_data[symbol] = []
            self.data[symbol] = {
                "spread_pct_data": [],
                "mexc_price": None,
                "dex_price": None,
                "spread_pct": None,
                "net_token": ADDRESSES_DATA[symbol][0],
                "token_address": ADDRESSES_DATA[symbol][1],
                "msg": None,
                "instruction_open": None,
                "instruction_close": None,
                "in_position_long": False,
                "in_position_short": False,
            }

    @staticmethod
    def get_dex_pairs(data):
        return [
            (info["net_token"], info["token_address"])
            for info in data.values()
            if info["net_token"] and info["token_address"]
        ]

    async def fetch_prices(self, session, symbols: List[str], pairs: List[Tuple]) -> Dict[str, Tuple[float, float]]:
        try:
            mexc_prices = await get_mexc_prices(session, symbols)
            dex_prices = await get_dex_prices(session, pairs)
            return {
                symbol: (mexc_prices.get(symbol), dex_prices.get((ADDRESSES_DATA[symbol][0], ADDRESSES_DATA[symbol][1])))
                for symbol in symbols
            }
        except Exception as e:
            raise RuntimeError(f"Ошибка при получении цен: {e}")

    async def refresh_data(self, session, is_spread_updated_time):
        try:
            prices = await self.fetch_prices(session, SYMBOLS, self.pairs)
            for symbol, (mexc_price, dex_price) in prices.items():
                symbol_data = self.data[symbol]

                if not (mexc_price and dex_price):
                    continue

                try:
                    spread_pct = self.utils.calc_spread(mexc_price, dex_price, CALC_SPREAD_METHOD)
                    if not spread_pct:
                        print(f"Проблемы с расчетом спреда. Символ {symbol}")
                        continue

                    self.temporary_tik_data[symbol].append(spread_pct)
                    symbol_data.update({
                        "mexc_price": mexc_price,
                        "dex_price": dex_price,
                        "spread_pct": spread_pct
                    })

                    if is_spread_updated_time:
                        max_spread = max(self.temporary_tik_data[symbol])
                        min_spread = min(self.temporary_tik_data[symbol])
                        # debug:
                        # text_print = dedent(f"""\                            
                        #     💲 Cur Spread: {spread_pct}
                        #     💲 High Spread: {max_spread}
                        #     💲 Low Spread: {min_spread}
                        # """)
                        # print(text_print)
                        symbol_data["spread_pct_data"].append((spread_pct, max_spread, min_spread))
                        if len(symbol_data["spread_pct_data"]) > HIST_SPREAD_LIMIT:
                            symbol_data["spread_pct_data"] = symbol_data["spread_pct_data"][-HIST_SPREAD_LIMIT:]
                        self.temporary_tik_data[symbol] = []

                    msg = f"\U0001F4E2 [{symbol.replace("_USDT", "")}]: Spread: {spread_pct:.4f} %"
                    in_position_long, in_position_short = symbol_data["in_position_long"], symbol_data["in_position_short"]
                    instr_open, instr_close, in_position_long_ren, in_position_short_ren = self.signals.signals_collector(
                        symbol, symbol_data["spread_pct_data"], spread_pct, in_position_long, in_position_short
                    )

                    symbol_data.update({
                        "msg": msg,
                        "instruction_open": instr_open,
                        "instruction_close": instr_close,
                        "in_position_long": in_position_long_ren,
                        "in_position_short": in_position_short_ren,
                    })

                except Exception as ex:
                    print(f"[ERROR] refresh_data for symbol {symbol} failed: {ex}\n{traceback.format_exc()}")

        except Exception as ex:
            print(f"[ERROR] refresh_data: {ex}\n{traceback.format_exc()}")

class Main(DataFetcher):
    def __init__(self):
        super().__init__()  # ← Вызов конструктора родительского класса
        self.notifier_q = TelegramNotifier(
            token=BOT_TOKEN,
            chat_ids=[CHANEL_ID]  # твой chat_id или список chat_id'ов
        )
        self.connector = NetworkServices() 

    def reset_data(self):
        for symbol_data in self.data.values():
            symbol_data.update({
                "msg": None,
                "mexc_price": None,
                "dex_price": None,
                "spread_pct": None,
                "instruction_open": None,
                "instruction_close": None,
            })
        
    async def msg_collector(self, is_text_refresh_time: bool) -> None:
        """Collects and sends messages based on symbol data and conditions."""

        async def send_signal(msg, plot_bytes=None, auto_delete=None, disable_notification=True):
            await self.notifier_q.send(
                msg,
                photo_bytes=plot_bytes,
                auto_delete=auto_delete,
                disable_notification=disable_notification
            )

        def prepare_signal_message(symbol, symbol_data, position_side, action):
            mexc_price = symbol_data.get("mexc_price")
            dex_price = symbol_data.get("dex_price")
            token_address = symbol_data.get("token_address")
            net_token = symbol_data.get("net_token")
            spread_pct = symbol_data["spread_pct"]
            
            return self.utils.format_signal_message(
                symbol, position_side, action, spread_pct, mexc_price, dex_price, token_address, net_token
            ) 

        for symbol in SYMBOLS:
            symbol_data = self.data.get(symbol)
            plot_bytes = None

            try:
                spread_pct = symbol_data.get("spread_pct")
                spread_pct_data = symbol_data.get("spread_pct_data")
                instruction_open = symbol_data.get("instruction_open", [])
                instruction_close = symbol_data.get("instruction_close", [])
                is_instruction = bool(instruction_open) or bool(instruction_close)

                if spread_pct is None:
                    print("spread_pct is None")
                    continue

                # Send regular update message if applicable
                if is_text_refresh_time or is_instruction:
                    # print("is_text_refresh_time or is_instruction")
                    # Generate plot once if needed
                    plot_bytes = self.utils.generate_plot_image(spread_pct_data, style=2 if is_text_refresh_time else 1)

                if is_text_refresh_time:
                    msg = symbol_data.get("msg")                    
                    await send_signal(msg, plot_bytes=plot_bytes, auto_delete=TEXT_REFRESH_INTERVAL + 2)

                if not is_instruction:
                    continue

                # Отправка сигналов на открытие
                for position_side, _ in instruction_open:
                    msg = prepare_signal_message(symbol, symbol_data, position_side, "is_opening")
                    await send_signal(msg, plot_bytes=plot_bytes, disable_notification=False)

                # Отправка сигналов на закрытие
                for position_side, _ in instruction_close:
                    msg = prepare_signal_message(symbol, symbol_data, position_side, "is_closing")
                    await send_signal(msg, plot_bytes=plot_bytes, disable_notification=False)

            except Exception as ex:
                print(f"[ERROR] msg_collector for symbol {symbol} failed: {ex}\n{traceback.format_exc()}")

            finally:
                if len(SYMBOLS) > 1:
                    await asyncio.sleep(0.25)

    async def _run(self):
        await self.connector.initialize_session()
        if not await self.connector.validate_session():
            raise ConnectionError("Не удалось установить сессию.")  

        check_session_counter = 0   
        refresh_counter = 0

        session = self.connector.session
        
        while True:
            try:
                if check_session_counter == 120:
                    check_session_counter = 0
                    if not await self.connector.validate_session():
                        print("Ошибка: Сессия неактивна даже после реконнекта.")
                        await self.connector.shutdown_session()                        
                        await asyncio.sleep(900)
                        continue
                    
                    session = self.connector.session 

                if refresh_counter < PRICE_REFRESH_INTERVAL:                                
                    continue
                else:
                    refresh_counter = 0

                is_data_refresh_time = self.utils.is_new_interval(DATA_REFRESH_INTERVAL)
                is_text_refresh_time = (
                    is_data_refresh_time if DATA_REFRESH_INTERVAL == TEXT_REFRESH_INTERVAL
                    else self.utils.is_new_interval(TEXT_REFRESH_INTERVAL)
                )

                await self.refresh_data(session, is_data_refresh_time)
                await self.msg_collector(is_text_refresh_time)

            except Exception as ex:
                print(f"[ERROR] Inner loop: {ex}")
                traceback.print_exc()
                raise

            finally:
                refresh_counter += 1
                check_session_counter += 1
                self.reset_data()      
                await asyncio.sleep(1)

if __name__ == "__main__":
    print("Start Bot")
    try:
        asyncio.run(Main()._run())
    except KeyboardInterrupt:
        print("Остановка по Ctrl+C")
