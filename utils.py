from datetime import datetime, timezone
from textwrap import dedent
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from scipy.interpolate import PchipInterpolator  # монотонная интерполяция
import numpy as np
# import math
import io

PRECISION = 30

def to_human_digit(value):
    # if value == 0:
    #     return "0.0"

    # abs_val = abs(value)
    # int_digits = int(math.log10(abs_val)) + 1 if abs_val >= 1 else 0
    # precision = max(0, 30 - int_digits)
    return f"{value:.{PRECISION}f}".rstrip('0').rstrip('.')

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
            💲 MEXC Price: {to_human_digit(mexc_price)}
            💲 DEX Price: {to_human_digit(dex_price)}
            📊 MEXC: https://www.mexc.com/ru-RU/futures/{symbol}_USDT?type=linear_swap
            🧪 Dexscreener: https://dexscreener.com/{net_token}/{token_address}
        """)

    def generate_plot_image(self, spread_data: list[tuple[float, float, float]], style: int = 1) -> bytes:
        if not spread_data or len(spread_data) < 4:
            return None

        spreads = spread_data[-self.plot_window:]

        plt.figure(figsize=(10, 5))
        plt.axhline(0, color='gray', linestyle='--', linewidth=1)

        if style == 1:
            # Подставляем high, если spread >= 0, иначе low
            y = [s[1] if s[0] >= 0 else s[2] for s in spreads]
            x = np.arange(len(y))
            x_new = np.linspace(x.min(), x.max(), 100)
            interpolator = PchipInterpolator(x, y)
            y_smooth = interpolator(x_new)
            plt.plot(x_new, y_smooth, color='green')

        elif style == 2:           
            ax = plt.gca()
            width = 0.6
            previous_close = spreads[0][0]

            highs = []
            lows = []

            for i, (close, high, low) in enumerate(spreads):
                open_price = previous_close
                color = 'green' if close >= open_price else 'red'

                lower = min(open_price, close)
                upper = max(open_price, close)
                height = upper - lower
                rect = patches.Rectangle((i - width / 2, lower), width, height, color=color, alpha=0.9, zorder=2)
                ax.add_patch(rect)

                # Верхняя тень (от тела до high)
                if high > upper:
                    ax.plot([i, i], [upper, high], color=color, linewidth=2, zorder=1)

                # Нижняя тень (от тела до low)
                if low < lower:
                    ax.plot([i, i], [low, lower], color=color, linewidth=2, zorder=1)

                highs.append(high)
                lows.append(low)
                previous_close = close

            ax.set_xlim(-1, len(spreads))
            ax.set_xticks([])

            y_min = min(lows)
            y_max = max(highs)
            y_range = y_max - y_min
            ax.set_ylim(y_min - y_range * 0.05, y_max + y_range * 0.05)

        else:
            raise ValueError("Недопустимый стиль. Используйте значение от 1 до 2.")

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