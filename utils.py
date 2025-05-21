from datetime import datetime, timezone
from textwrap import dedent
import matplotlib.pyplot as plt
# from scipy.interpolate import make_interp_spline
from scipy.interpolate import PchipInterpolator  # монотонная интерполяция
import numpy as np
import io

class Utils():
    def __init__(self, plot_window):  
        self.last_fetch_timestamps = {}
        self.plot_window = plot_window
    
    @staticmethod
    def format_signal_message(symbol, position_side, action, spread, mexc_price, dex_price, token_address, net_token):
        if action == "is_opening":
            action_msg = "Открываем"
            emoji = "🟢" if position_side == "LONG" else "🔴"
        elif action == "is_closing":
            action_msg = "Закрываем"
            emoji = "🔒"
        else:
            action_msg = "Действие"
            emoji = "⚠️"

        return dedent(f"""\
            {emoji} [{symbol.replace("_USDT", "")}][{action_msg}][{position_side}]
            ⚖️ Spread: {spread:.4f}%
            💲 MEXC Price: {mexc_price}
            💲 DEX Price: {dex_price}
            📊 MEXC: https://www.mexc.com/ru-RU/futures/{symbol}_USDT?type=linear_swap
            🧪 Dexscreener: https://dexscreener.com/{net_token}/{token_address}
        """)

    def generate_plot_image(self, spread_data: list[tuple[str, float]], style: int = 1) -> bytes:
        if (not spread_data) or (len(spread_data) < 4):
            return None

        # Используем только последние N значений
        spreads = spread_data[-self.plot_window:]

        plt.figure(figsize=(8, 4))
        plt.axhline(0, color='gray', linestyle='--', linewidth=1)

        if style == 0:
            plt.plot(spreads, marker='o', linestyle='-', color='blue')

        elif style == 1:
            x = np.arange(len(spreads))
            x_new = np.linspace(x.min(), x.max(), 100)  # 100 точек для плавности и стабильности
            interpolator = PchipInterpolator(x, spreads)
            y_smooth = interpolator(x_new)
            plt.plot(x_new, y_smooth, color='green')

        elif style == 2:
            plt.bar(range(len(spreads)), spreads, color='purple')

        elif style == 3:
            plt.scatter(range(len(spreads)), spreads, color='orange')

        elif style == 4:
            plt.plot(spreads, color='red')
            plt.fill_between(range(len(spreads)), spreads, 0, alpha=0.3, color='red')

        else:
            raise ValueError("Недопустимый стиль. Используйте значение от 0 до 4.")

        plt.title("История Spread (%)")
        plt.ylabel("Spread %")
        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format='png')
        plt.close()
        buffer.seek(0)
        return buffer.read()
    
    @staticmethod
    def calc_spread(price_a: float, price_b: float, method: str = 'a') -> float:
        if not (price_a and price_b):
            return None
        if method == 'a':
            return (price_a - price_b) / price_a * 100
        elif method == 'b':
            return (price_a - price_b) / price_b * 100
        elif method == 'ratio':
            return (price_a / price_b - 1) * 100
        else:
            raise ValueError(f"Unknown method '{method}'. Choose from 'a', 'b', or 'ratio'.")
        
    def is_new_interval(self, refresh_interval: int) -> bool:
        now = datetime.now(timezone.utc)
        current_timestamp = int(now.timestamp())
        nearest_timestamp = (current_timestamp // refresh_interval) * refresh_interval

        last_timestamp = self.last_fetch_timestamps.get(refresh_interval)

        if last_timestamp is None or nearest_timestamp > last_timestamp:
            self.last_fetch_timestamps[refresh_interval] = nearest_timestamp
            return True
        return False