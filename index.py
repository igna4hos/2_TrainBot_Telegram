import matplotlib.pyplot as plt
import pandas as pd
import requests
import telebot
import os
import io
from difflib import get_close_matches
from datetime import datetime, date, time
from telebot import types

TOKEN = "Telegram_token"
OPENWEATHER_TOKEN = "Openweather_token"
bot = telebot.TeleBot(TOKEN)

CSV_FILE = "users.csv"
FOOD_CSV = "caloric_products.csv"
WATER_LOG_CSV = "water_log.csv"
FOOD_LOG_CSV = "food_log.csv"
users_state = {}  # временно храним данные при заполнении информации о пользователе
food_state = {}  # временно храним информацию о блюде


def load_users():
	if os.path.exists(CSV_FILE):
		return pd.read_csv(CSV_FILE)
	else:
		return pd.DataFrame(columns=[
			"user_id", "gender", "weight", "height", "age",
			"activity", "city", "water_goal", "calorie_goal",
			"logged_water", "logged_calories", "burned_calories",
			"last_reset_date"
		])

def save_user(data):
	df = load_users()
	df = df[df.user_id != data["user_id"]]
	df = pd.concat([df, pd.DataFrame([data])], ignore_index=True)
	df.to_csv(CSV_FILE, index=False)


@bot.message_handler(commands=["start"])
def start(message):
	user = message.from_user
	text = (
		f"Привет, {user.first_name or 'друг'}! 👋\n"
		"Я помогу тебе следить за твоей активностью.\n"
		"Для начала командой /set_profile заполни информацию о себе.\n"
		"Напиши /help, чтобы увидеть список команд."
	)

	keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
	btn_top = types.KeyboardButton("📈 Прогресс")
	btn_myfav = types.KeyboardButton("📊 Статистика")
	keyboard.add(btn_top, btn_myfav)

	bot.send_message(message.chat.id, text, reply_markup=keyboard)


@bot.message_handler(commands=["help"])
def help_command(message):
	text = (
		"Доступные команды:\n"
		"/start – приветствие\n"
		"/help – список команд\n"
		"/set_profile – настройка профиля пользователя\n"
		"/log_water <мл> – сохраняем объём выпитой воды\n"
		"/log_food <название продукта> – записываем еду, которую вы съели\n"
		"/log_workout <тип> <минуты> – фиксируем сожжённые калории\n"
		"/check_progress – показывает, сколько воды и калорий потреблено, сожжено и сколько осталось до выполнения цели\n"
		"/stats – выводим графики потребления воды и съеденной еды\n"
		"/tip – подсказки по здоровью\n"
	)
	bot.send_message(message.chat.id, text)


@bot.message_handler(func=lambda m: m.text in ["📈 Прогресс", "📊 Статистика"])
def keyboard_buttons(message):
	if message.text == "📈 Прогресс":
		check_progress(message)
	elif message.text == "📊 Статистика":
		stats(message)


def reset_daily_if_needed(user_id):
	df = load_users()
	today = date.today().isoformat()

	user = df[df.user_id == user_id]
	if user.empty:
		return

	last_reset = user.iloc[0]["last_reset_date"]

	if last_reset != today:
		df.loc[df.user_id == user_id, [
			"logged_water",
			"logged_calories",
			"burned_calories"
		]] = 0

		df.loc[df.user_id == user_id, "last_reset_date"] = today
		df.to_csv(CSV_FILE, index=False)

def calculate_bmr(gender, weight, height, age):
	if gender == "m":
		return 10 * weight + 6.25 * height - 5 * age + 5
	else:
		return 10 * weight + 6.25 * height - 5 * age - 161

def activity_multiplier(minutes):
	if minutes < 20:
		return 1.2
	elif minutes < 40:
		return 1.375
	elif minutes < 60:
		return 1.55
	elif minutes < 90:
		return 1.725
	else:
		return 1.9

def water_norm(weight):
	return weight * 30


@bot.message_handler(commands=["set_profile"])
def set_profile(message):
	users_state[message.chat.id] = {"user_id": message.chat.id}

	markup = types.InlineKeyboardMarkup()
	markup.add(
		types.InlineKeyboardButton("👨 Мужской", callback_data="gender_m"),
		types.InlineKeyboardButton("👩 Женский", callback_data="gender_f")
	)

	bot.send_message(
		message.chat.id,
		"Укажите ваш пол:",
		reply_markup=markup
	)


@bot.callback_query_handler(func=lambda call: call.data.startswith("gender_"))
def callback_set_gender(call):
	gender = call.data.split("_")[1]
	users_state[call.message.chat.id]["gender"] = gender

	bot.edit_message_reply_markup(
		call.message.chat.id,
		call.message.message_id
	)
	bot.send_message(call.message.chat.id, f"Ваш пол: {'мужской' if gender == 'm' else 'женский'}")
	bot.send_message(call.message.chat.id, "Введите ваш вес (кг):")
	bot.register_next_step_handler(call.message, set_weight)

def set_weight(message):
	users_state[message.chat.id]["weight"] = float(message.text)
	bot.send_message(message.chat.id, "Введите ваш рост (см):")
	bot.register_next_step_handler(message, set_height)

def set_height(message):
	users_state[message.chat.id]["height"] = int(message.text)
	bot.send_message(message.chat.id, "Введите ваш возраст:")
	bot.register_next_step_handler(message, set_age)

def set_age(message):
	users_state[message.chat.id]["age"] = int(message.text)
	bot.send_message(message.chat.id, "Сколько минут активности у вас в день?")
	bot.register_next_step_handler(message, set_activity)

def set_activity(message):
	users_state[message.chat.id]["activity"] = int(message.text)
	bot.send_message(message.chat.id, "В каком городе вы находитесь?")
	bot.register_next_step_handler(message, set_city)

def set_city(message):
	users_state[message.chat.id]["city"] = message.text

	markup = types.InlineKeyboardMarkup()
	markup.add(
		types.InlineKeyboardButton("✍ Указать норму", callback_data="calories_manual"),
		types.InlineKeyboardButton("⚙ Автоматически", callback_data="calories_auto")
	)

	bot.send_message(
		message.chat.id,
		"Как задать цель по калориям?",
		reply_markup=markup
	)

@bot.callback_query_handler(func=lambda call: call.data.startswith("calories_"))
def callback_calories_mode(call):
	bot.edit_message_reply_markup(
		call.message.chat.id,
		call.message.message_id
	)

	if call.data == "calories_manual":
		bot.send_message(call.message.chat.id, "Введите желаемую норму калорий:")
		bot.register_next_step_handler(call.message, set_manual_calories)
	else:
		calculate_auto_calories(call.message)

def set_manual_calories(message):
	users_state[message.chat.id]["calorie_goal"] = int(message.text)
	finalize_profile(message)

def calculate_auto_calories(message):
	u = users_state[message.chat.id]
	bmr = calculate_bmr(u["gender"], u["weight"], u["height"], u["age"])
	multiplier = activity_multiplier(u["activity"])
	u["calorie_goal"] = int(bmr * multiplier)
	finalize_profile(message)

def finalize_profile(message):
	user_local = users_state[message.chat.id]

	user_local["water_goal"] = water_norm(user_local["weight"])
	user_local["logged_water"] = 0
	user_local["logged_calories"] = 0
	user_local["burned_calories"] = 0
	user_local["last_reset_date"] = date.today().isoformat()

	save_user(user_local)

	bot.send_message(
		message.chat.id,
		f"Профиль сохранён ✅\n"
		f"🔥 Калории: {user_local['calorie_goal']} ккал\n"
		f"💧 Вода: {user_local['water_goal']} мл"
	)


@bot.message_handler(commands=["log_water"])
def log_water(message):
	reset_daily_if_needed(message.chat.id)
	try:
		amount = int(message.text.split()[1])
	except (IndexError, ValueError):
		bot.send_message(message.chat.id, "Использование: /log_water <мл>")
		return

	df = load_users()
	user = df[df.user_id == message.chat.id]

	if user.empty:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	logged = int(user.iloc[0]["logged_water"]) + amount
	goal = int(user.iloc[0]["water_goal"])

	df.loc[df.user_id == message.chat.id, "logged_water"] = logged
	df.to_csv(CSV_FILE, index=False)

	append_water_log(message.chat.id, amount)

	remaining = max(goal - logged, 0)

	bot.send_message(
		message.chat.id,
		f"💧 Выпито: {logged} мл\n"
		f"🎯 Осталось до нормы: {remaining} мл"
	)


def get_city_temperature(city):
	url = (
		"https://api.openweathermap.org/data/2.5/weather"
		f"?q={city}&appid={OPENWEATHER_TOKEN}&units=metric&lang=ru"
	)
	response = requests.get(url)
	if response.status_code == 200:
		data = response.json()
		return data["main"]["temp"]
	else:
		print(f"Ошибка: {response.status_code}")
		return None

@bot.message_handler(commands=["log_workout"])
def log_workout(message):
	reset_daily_if_needed(message.chat.id)
	try:
		_, train_type, minutes = message.text.split()
		minutes = int(minutes)
	except ValueError:
		bot.send_message(
			message.chat.id,
			"Использование: /log_workout <тип> <минуты>\nПример: /log_workout бег 30"
		)
		return

	users_df = load_users()
	user = users_df[users_df.user_id == message.chat.id]

	if user.empty:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	train_df = pd.read_csv("train_expenses.csv")

	user_input = train_type.strip().lower()
	train_types = train_df["train_type"].str.strip().str.lower().tolist()

	# При вводе названия тренировки можно промахнуться при вводе и программа поймёт вид занятия
	matches = get_close_matches(
		user_input,
		train_types,
		n=1,
		cutoff=0.6
	)

	if matches:
		matched_type = matches[0]
		train = train_df[
			train_df.train_type.str.strip().str.lower() == matched_type
		].iloc[0]
		display_train_name = train.train_type
	else:
		train = train_df.iloc[0]
		display_train_name = train_type.capitalize()

	calories_burned = int(train.calorie_consumption * minutes / 60)
	water_needed = int(train.water_train * minutes / 60)
	extra_water = 0

	city = user.iloc[0]["city"]
	temp = get_city_temperature(city)

	# При температуре более 25 градусов рекомендуется выпить дополнительно воду
	if temp > 25:
		extra_water = int(train.water_add_heat * minutes / 60)

	total_water = water_needed + extra_water

	users_df.loc[users_df.user_id == message.chat.id, "burned_calories"] += calories_burned
	users_df.to_csv(CSV_FILE, index=False)

	bot.send_message(
		message.chat.id,
		f"💪🏼 {display_train_name} {minutes} минут — {calories_burned} ккал\n"
		f"💧 Дополнительно: выпейте {total_water} мл\n"
		f"🌡 Температура в городе {city}: {temp:.1f}°C"
	)


def get_food_info(product_name):
	url = (
		"https://world.openfoodfacts.org/cgi/search.pl"
		f"?action=process&search_terms={product_name}&json=true&page_size=5"
	)
	response = requests.get(url, timeout=10)

	if response.status_code != 200:
		return None

	data = response.json()
	products = data.get("products", [])

	for product in products:
		calories = product.get("nutriments", {}).get("energy-kcal_100g")
		name = product.get("product_name")

		if calories and name:
			return {
				"name": name,
				"calories": float(calories)
			}

	return None

def get_food_from_csv(product_name):
	df = pd.read_csv(FOOD_CSV)

	user_input = product_name.strip().lower()
	products = df["product_name"].str.strip().str.lower().tolist()

	matches = get_close_matches(user_input, products, n=1, cutoff=0.6)

	if matches:
		matched = matches[0]
		row = df[df.product_name.str.lower() == matched].iloc[0]
		return {
			"name": row.product_name,
			"calories": float(row.energy_kcal_100g)
		}

	return None


@bot.message_handler(commands=["log_food"])
def log_food(message):
	reset_daily_if_needed(message.chat.id)
	try:
		product_name = message.text.split(" ", 1)[1]
	except IndexError:
		bot.send_message(message.chat.id, "Использование: /log_food <название продукта>")
		return

	user_df = load_users()
	if user_df[user_df.user_id == message.chat.id].empty:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	# 1. Сначала обращаемся к OpenFoodFacts
	food = get_food_info(product_name)

	# 2. Затем пытаемся найти позицию в файле
	if not food:
		food = get_food_from_csv(product_name)

	# 3. Позиция найдена
	if food:
		food_state[message.chat.id] = food

		bot.send_message(
			message.chat.id,
			f"🍽 {food['name']} — {food['calories']} ккал на 100 г.\n"
			f"Сколько грамм вы съели?"
		)
		bot.register_next_step_handler(message, ask_food_weight)

	# 4. Не унываем. Пользователь сам введёт калорийность
	else:
		bot.send_message(
			message.chat.id,
			"Не удалось найти продукт 😕\n"
			"Введите количество съеденных калорий:"
		)
		bot.register_next_step_handler(message, ask_manual_calories)


def ask_food_weight(message):
	try:
		grams = float(message.text)
	except ValueError:
		bot.send_message(message.chat.id, "Введите число (граммы):")
		bot.register_next_step_handler(message, ask_food_weight)
		return

	food = food_state.pop(message.chat.id)
	calories = round(food["calories"] * grams / 100, 1)

	df = load_users()
	df.loc[df.user_id == message.chat.id, "logged_calories"] += calories
	df.to_csv(CSV_FILE, index=False)

	append_food_log(message.chat.id, calories)

	bot.send_message(
		message.chat.id,
		f"✅ Записано: {calories} ккал"
	)

def ask_manual_calories(message):
	try:
		calories = float(message.text)
	except ValueError:
		bot.send_message(message.chat.id, "Введите число (ккал):")
		bot.register_next_step_handler(message, ask_manual_calories)
		return

	df = load_users()
	df.loc[df.user_id == message.chat.id, "logged_calories"] += calories
	df.to_csv(CSV_FILE, index=False)

	append_food_log(message.chat.id, calories)

	bot.send_message(
		message.chat.id,
		f"✅ Записано вручную: {calories} ккал"
	)


@bot.message_handler(commands=["check_progress"])
def check_progress(message):
	df = load_users()
	user = df[df.user_id == message.chat.id]

	if user.empty:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return
	reset_daily_if_needed(message.chat.id)
	u = user.iloc[0]

	# Лимиты на воду
	water_logged = float(u.logged_water)
	water_goal = float(u.water_goal)
	water_left = max(water_goal - water_logged, 0)

	# Лимиты на калории
	calories_logged = float(u.logged_calories)
	calorie_goal = float(u.calorie_goal)
	calories_left = max(calorie_goal - calories_logged, 0)

	burned = float(u.burned_calories)

	bot.send_message(
		message.chat.id,
		"📊 Прогресс:\n\n"
		"💧 Вода:\n"
		f"- Выпито: {int(water_logged)} мл из {int(water_goal)} мл\n"
		f"- Осталось: {int(water_left)} мл\n\n"
		"🔥 Калории:\n"
		f"- Потреблено: {int(calories_logged)} ккал из {int(calorie_goal)} ккал\n"
		f"- Осталось: {int(calories_left)} ккал\n"
		f"🏃‍♂️ Сожжено: {int(burned)} ккал"
	)


def reset_daily_if_needed(user_id):
	df = load_users()
	today = date.today().isoformat()

	user = df[df.user_id == user_id]
	if user.empty:
		return

	last_reset = user.iloc[0]["last_reset_date"]

	if last_reset != today:
		df.loc[df.user_id == user_id, [
			"logged_water",
			"logged_calories",
			"burned_calories"
		]] = 0

		df.loc[df.user_id == user_id, "last_reset_date"] = today
		df.to_csv(CSV_FILE, index=False)


def append_water_log(user_id, amount):
	row = {
		"user_id": user_id,
		"datetime": datetime.now().isoformat(),
		"amount_ml": amount
	}

	df = pd.DataFrame([row])
	if os.path.exists(WATER_LOG_CSV):
		df.to_csv(WATER_LOG_CSV, mode="a", header=False, index=False)
	else:
		df.to_csv(WATER_LOG_CSV, index=False)

def append_food_log(user_id, calories):
	row = {
		"user_id": user_id,
		"datetime": datetime.now().isoformat(),
		"calories": calories
	}

	df = pd.DataFrame([row])
	if os.path.exists(FOOD_LOG_CSV):
		df.to_csv(FOOD_LOG_CSV, mode="a", header=False, index=False)
	else:
		df.to_csv(FOOD_LOG_CSV, index=False)


def send_plot_as_photo(chat_id):
	buf = io.BytesIO()
	plt.savefig(buf, format="png")
	buf.seek(0)
	plt.close()
	bot.send_photo(chat_id, buf)

@bot.message_handler(commands=["stats"])
def stats(message):
	user_id = message.chat.id
	today_start = datetime.combine(date.today(), time.min)

	users_df = load_users()
	user = users_df[users_df.user_id == user_id]

	if user.empty:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	water_goal = float(user.iloc[0]["water_goal"])
	calorie_goal = float(user.iloc[0]["calorie_goal"])

	# График по воде
	if os.path.exists(WATER_LOG_CSV):
		water_df = pd.read_csv(WATER_LOG_CSV)
		water_df["datetime"] = pd.to_datetime(water_df["datetime"])

		water_df = water_df[
			(water_df.user_id == user_id) &
			(water_df.datetime >= today_start)
		]

		if not water_df.empty:
			water_df["cumulative"] = water_df.amount_ml.cumsum()

			plt.figure()
			plt.plot(water_df.datetime, water_df.cumulative, marker="o")
			plt.axhline(water_goal, linestyle="--")
			plt.title("Прогресс выпитой воды за день")
			plt.xlabel("Время")
			plt.ylabel("мл")
			plt.tight_layout()

			send_plot_as_photo(message.chat.id)

	# График по калориям
	if os.path.exists(FOOD_LOG_CSV):
		food_df = pd.read_csv(FOOD_LOG_CSV)
		food_df["datetime"] = pd.to_datetime(food_df["datetime"])

		food_df = food_df[
			(food_df.user_id == user_id) &
			(food_df.datetime >= today_start)
		]

		if not food_df.empty:
			food_df["cumulative"] = food_df.calories.cumsum()

			plt.figure()
			plt.plot(food_df.datetime, food_df.cumulative, marker="o")
			plt.axhline(calorie_goal, linestyle="--")
			plt.title("Прогресс по калориям за день")
			plt.xlabel("Время")
			plt.ylabel("ккал")
			plt.tight_layout()

			send_plot_as_photo(message.chat.id)


@bot.message_handler(commands=["tip"])
def tip(message):
	reset_daily_if_needed(message.chat.id)

	df = load_users()
	user = df[df.user_id == message.chat.id]

	if user.empty:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	u = user.iloc[0]

	calories_logged = float(u.logged_calories)
	calorie_goal = float(u.calorie_goal)

	delta = calorie_goal - calories_logged

	# Когда осталось место в ежедневной норме калорий
	if delta > 0:
		if not os.path.exists("health_food.csv"):
			bot.send_message(message.chat.id, "Файл health_food.csv не найден")
			return

		food_df = pd.read_csv("health_food.csv")

		# Каждый раз будет выборка из 3 разных позиций
		sample_size = min(3, len(food_df))
		recommendations = food_df.sample(sample_size)

		text = (
			"🥗 Вам можно ещё поесть!\n"
			f"До цели осталось: {int(delta)} ккал\n\n"
			"Рекомендации:\n"
		)

		for _, row in recommendations.iterrows():
			text += f"• {row.product_name} — {row.energy_kcal_100g} ккал / 100 г\n"

		bot.send_message(message.chat.id, text)
		return

	# Когда мы переели, то нужно предложить способ сжечь калории
	excess = abs(delta)

	if excess <= 500:
		burn_rate = 350  # Сжимаемые калории за час тренировки
		activity = "🚶‍♂️ Быстрая ходьба"
	else:
		burn_rate = 680
		activity = "🏃‍♂️ Бег"

	minutes = int((excess / burn_rate) * 60)
	minutes = min(minutes, 90) # Более 1,5 часов не предлагать тренировку

	bot.send_message(
		message.chat.id,
		f"🔥 Вы превысили норму на {int(excess)} ккал\n"
		f"{activity}\n"
		f"⏱ Рекомендуемое время: {minutes} минут"
	)


def main():
	bot.infinity_polling()

if __name__ == "__main__":
	main()