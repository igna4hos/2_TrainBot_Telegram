import os
os.environ["MPLCONFIGDIR"] = "/tmp/matplotlib"
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests
import telebot
import io
import json
import logging
import boto3
from difflib import get_close_matches
from datetime import datetime, date, time
from telebot import types
from functools import wraps

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENWEATHER_TOKEN = os.environ.get("OPENWEATHER_TOKEN")
ACCESS_KEY_ID = os.environ.get("ACCESS_KEY_ID")
SECRET_ACCESS_KEY = os.environ.get("SECRET_ACCESS_KEY")
DEPLOY_VERSION = os.environ.get("BOT_DEPLOY_VERSION")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "fitnesstrainer-storage")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

session = boto3.session.Session()
s3_client = session.client(
	service_name='s3',
	endpoint_url='https://storage.yandexcloud.net',
	aws_access_key_id=ACCESS_KEY_ID,
	aws_secret_access_key=SECRET_ACCESS_KEY
)

users_state = {}
food_state = {}

CSV_FILE = "users.csv"
FOOD_CSV = "caloric_products.csv"
WATER_LOG_CSV = "water_log.csv"
FOOD_LOG_CSV = "food_log.csv"
TRAIN_CSV = "train_expenses.csv"
HEALTH_FOOD_CSV = "health_food.csv"

logging.basicConfig(
	level=logging.INFO,
	format="%(levelname)s - %(message)s",
	force=True
)
logger = logging.getLogger("bot")
if DEPLOY_VERSION:
	logger.info("БОТ ЗАПУЩЕН. Версия = %s", DEPLOY_VERSION)

def log_message(func):
	@wraps(func)
	def wrapper(message, *args, **kwargs):
		logger.info(
			"Получено сообщение: %s",
			message.text
		)
		return func(message, *args, **kwargs)
	return wrapper

def download_from_s3(file_key):
	try:
		response = s3_client.get_object(Bucket=BUCKET_NAME, Key=file_key)
		return response['Body'].read().decode('utf-8')
	except Exception as e:
		logger.exception(f"Error downloading {file_key}: {e}")
		return None

def upload_to_s3(file_key, content):
	try:
		s3_client.put_object(
			Bucket=BUCKET_NAME,
			Key=file_key,
			Body=content,
			ContentType='text/csv'
		)
		return True
	except Exception as e:
		logger.exception(f"Error uploading {file_key}: {e}")
		return False

def load_df_from_s3(file_key):
	content = download_from_s3(file_key)
	if content:
		return pd.read_csv(io.StringIO(content))
	return pd.DataFrame()

def save_df_to_s3(df, file_key):
	csv_buffer = io.StringIO()
	df.to_csv(csv_buffer, index=False)
	upload_to_s3(file_key, csv_buffer.getvalue())

def load_users():
	return load_df_from_s3(CSV_FILE)

def save_user(data):
	df = load_users()
	if not df.empty and "user_id" in df.columns:
		df = df[df.user_id != data["user_id"]]
	df = pd.concat([df, pd.DataFrame([data])], ignore_index=True)
	save_df_to_s3(df, CSV_FILE)

def reset_daily_if_needed(user_id):
	df = load_users()
	if df.empty:
		return

	today = date.today().isoformat()

	if user_id in df['user_id'].values:
		user = df[df.user_id == user_id].iloc[0]
		last_reset = user.get("last_reset_date", "")
		
		if last_reset != today:
			mask = df.user_id == user_id
			df.loc[mask, ["logged_water", "logged_calories", "burned_calories"]] = 0
			df.loc[mask, "last_reset_date"] = today
			save_df_to_s3(df, CSV_FILE)

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

def get_city_temperature(city):
	url = (
		"https://api.openweathermap.org/data/2.5/weather"
		f"?q={city}&appid={OPENWEATHER_TOKEN}&units=metric&lang=ru"
	)
	try:
		response = requests.get(url, timeout=5)
		if response.status_code == 200:
			data = response.json()
			return data["main"]["temp"]
	except Exception as e:
		logger.error(f"Error getting temperature: {e}")
	return None

def get_food_info(product_name):
	url = (
		"https://world.openfoodfacts.org/cgi/search.pl"
		f"?action=process&search_terms={product_name}&json=true&page_size=5"
	)
	try:
		response = requests.get(url, timeout=10)
		if response.status_code == 200:
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
	except Exception as e:
		logger.error(f"Error getting food info: {e}")
	return None

def get_food_from_csv(product_name):
	df = load_df_from_s3(FOOD_CSV)
	if df.empty:
		return None

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

def append_water_log(user_id, amount):
	water_df = load_df_from_s3(WATER_LOG_CSV)

	row = {
		"user_id": user_id,
		"datetime": datetime.now().isoformat(),
		"amount_ml": amount
	}

	new_df = pd.DataFrame([row])
	if not water_df.empty:
		water_df = pd.concat([water_df, new_df], ignore_index=True)
	else:
		water_df = new_df

	save_df_to_s3(water_df, WATER_LOG_CSV)

def append_food_log(user_id, calories):
	food_df = load_df_from_s3(FOOD_LOG_CSV)

	row = {
		"user_id": user_id,
		"datetime": datetime.now().isoformat(),
		"calories": calories
	}

	new_df = pd.DataFrame([row])
	if not food_df.empty:
		food_df = pd.concat([food_df, new_df], ignore_index=True)
	else:
		food_df = new_df

	save_df_to_s3(food_df, FOOD_LOG_CSV)

def send_plot_as_photo(chat_id, plot_func):
	try:
		buf = io.BytesIO()
		plot_func()
		plt.savefig(buf, format="png", dpi=100)
		buf.seek(0)
		plt.close()
		bot.send_photo(chat_id, buf)
		buf.close()
	except Exception as e:
		bot.send_message(chat_id, f"Ошибка при создании графика: {str(e)}")
		logger.error(f"Plot error: {e}")

@bot.message_handler(commands=["start"])
@log_message
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
@log_message
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
		"/profile - информация об аккаунте\n"
		"/stats – выводим графики потребления воды и съеденной еды\n"
		"/tip – подсказки по здоровью\n"
	)
	bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text in ["📈 Прогресс", "📊 Статистика"])
@log_message
def keyboard_buttons(message):
	if message.text == "📈 Прогресс":
		check_progress(message)
	elif message.text == "📊 Статистика":
		stats(message)

@bot.message_handler(commands=["set_profile"])
@log_message
def set_profile(message):
	users_state[message.chat.id] = {"user_id": message.chat.id}

	markup = types.InlineKeyboardMarkup()
	markup.add(
		types.InlineKeyboardButton("👨 Мужской", callback_data="gender_m"),
		types.InlineKeyboardButton("👩 Женский", callback_data="gender_f")
	)

	bot.send_message(message.chat.id, "Укажите ваш пол:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("gender_"))
def callback_set_gender(call):
	gender = call.data.split("_")[1]
	users_state[call.message.chat.id]["gender"] = gender

	bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id)
	bot.send_message(call.message.chat.id, f"Ваш пол: {'мужской' if gender == 'm' else 'женский'}")
	bot.send_message(call.message.chat.id, "Введите ваш вес (кг):")
	bot.register_next_step_handler(call.message, set_weight)

def set_weight(message):
	try:
		users_state[message.chat.id]["weight"] = float(message.text)
		bot.send_message(message.chat.id, "Введите ваш рост (см):")
		bot.register_next_step_handler(message, set_height)
	except ValueError:
		bot.send_message(message.chat.id, "Пожалуйста, введите число (например: 70)")
		bot.register_next_step_handler(message, set_weight)

def set_height(message):
	try:
		users_state[message.chat.id]["height"] = int(message.text)
		bot.send_message(message.chat.id, "Введите ваш возраст:")
		bot.register_next_step_handler(message, set_age)
	except ValueError:
		bot.send_message(message.chat.id, "Пожалуйста, введите целое число (например: 175)")
		bot.register_next_step_handler(message, set_height)

def set_age(message):
	try:
		users_state[message.chat.id]["age"] = int(message.text)
		bot.send_message(message.chat.id, "Сколько минут активности у вас в день?")
		bot.register_next_step_handler(message, set_activity)
	except ValueError:
		bot.send_message(message.chat.id, "Пожалуйста, введите целое число (например: 30)")
		bot.register_next_step_handler(message, set_age)

def set_activity(message):
	try:
		users_state[message.chat.id]["activity"] = int(message.text)
		bot.send_message(message.chat.id, "В каком городе вы находитесь?")
		bot.register_next_step_handler(message, set_city)
	except ValueError:
		bot.send_message(message.chat.id, "Пожалуйста, введите число (например: 60)")
		bot.register_next_step_handler(message, set_activity)

def set_city(message):
	users_state[message.chat.id]["city"] = message.text

	markup = types.InlineKeyboardMarkup()
	markup.add(
		types.InlineKeyboardButton("✍ Указать норму", callback_data="calories_manual"),
		types.InlineKeyboardButton("⚙ Автоматически", callback_data="calories_auto")
	)

	bot.send_message(message.chat.id, "Как задать цель по калориям?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("calories_"))
def callback_calories_mode(call):
	bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id)

	if call.data == "calories_manual":
		bot.send_message(call.message.chat.id, "Введите желаемую норму калорий:")
		bot.register_next_step_handler(call.message, set_manual_calories)
	else:
		calculate_auto_calories(call.message)

def set_manual_calories(message):
	try:
		users_state[message.chat.id]["calorie_goal"] = int(message.text)
		finalize_profile(message)
	except ValueError:
		bot.send_message(message.chat.id, "Пожалуйста, введите целое число (например: 2000)")
		bot.register_next_step_handler(message, set_manual_calories)

def calculate_auto_calories(message):
	user_local = users_state[message.chat.id]
	bmr = calculate_bmr(user_local["gender"], user_local["weight"], user_local["height"], user_local["age"])
	multiplier = activity_multiplier(user_local["activity"])
	user_local["calorie_goal"] = int(bmr * multiplier)
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
@log_message
def log_water(message):
	reset_daily_if_needed(message.chat.id)
	try:
		amount = int(message.text.split()[1])
	except (IndexError, ValueError):
		bot.send_message(message.chat.id, "Использование: /log_water <мл>")
		return

	df = load_users()
	if df.empty or message.chat.id not in df['user_id'].values:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	logged = int(df.loc[df.user_id == message.chat.id, "logged_water"].iloc[0]) + amount
	goal = int(df.loc[df.user_id == message.chat.id, "water_goal"].iloc[0])

	df.loc[df.user_id == message.chat.id, "logged_water"] = logged
	save_df_to_s3(df, CSV_FILE)

	append_water_log(message.chat.id, amount)

	remaining = max(goal - logged, 0)

	bot.send_message(
		message.chat.id,
		f"💧 Выпито: {logged} мл\n"
		f"🎯 Осталось до нормы: {remaining} мл"
	)

@bot.message_handler(commands=["log_workout"])
@log_message
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
	if users_df.empty or message.chat.id not in users_df['user_id'].values:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	train_df = load_df_from_s3(TRAIN_CSV)
	if train_df.empty:
		bot.send_message(message.chat.id, "База тренировок недоступна")
		return

	user_input = train_type.strip().lower()
	train_types = train_df["train_type"].str.strip().str.lower().tolist()

	matches = get_close_matches(user_input, train_types, n=1, cutoff=0.6)

	if matches:
		matched_type = matches[0]
		train = train_df[train_df.train_type.str.strip().str.lower() == matched_type].iloc[0]
		display_train_name = train.train_type
	else:
		train = train_df.iloc[0]
		display_train_name = train_type.capitalize()

	calories_burned = int(train.calorie_consumption * minutes / 60)
	water_needed = int(train.water_train * minutes / 60)
	extra_water = 0

	city = users_df.loc[users_df.user_id == message.chat.id, "city"].iloc[0]
	temp = get_city_temperature(city)

	if temp and temp > 25:
		extra_water = int(train.water_add_heat * minutes / 60)

	total_water = water_needed + extra_water

	users_df.loc[users_df.user_id == message.chat.id, "burned_calories"] += calories_burned
	save_df_to_s3(users_df, CSV_FILE)

	response_text = (
		f"💪🏼 {display_train_name} {minutes} минут — {calories_burned} ккал\n"
		f"💧 Дополнительно: выпейте {total_water} мл"
	)

	if temp:
		response_text += f"\n🌡 Температура в городе {city}: {temp:.1f}°C"

	bot.send_message(message.chat.id, response_text)

@bot.message_handler(commands=["log_food"])
@log_message
def log_food(message):
	reset_daily_if_needed(message.chat.id)
	try:
		product_name = message.text.split(" ", 1)[1]
	except IndexError:
		bot.send_message(message.chat.id, "Использование: /log_food <название продукта>")
		return

	user_df = load_users()
	if user_df.empty or message.chat.id not in user_df['user_id'].values:
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

	food = food_state.pop(message.chat.id, None)
	if not food:
		bot.send_message(message.chat.id, "Сессия устарела. Начните заново.")
		return

	calories = round(food["calories"] * grams / 100, 1)

	df = load_users()
	df.loc[df.user_id == message.chat.id, "logged_calories"] += calories
	save_df_to_s3(df, CSV_FILE)

	append_food_log(message.chat.id, calories)

	bot.send_message(message.chat.id, f"✅ Записано: {calories} ккал")

def ask_manual_calories(message):
	try:
		calories = float(message.text)
	except ValueError:
		bot.send_message(message.chat.id, "Введите число (ккал):")
		bot.register_next_step_handler(message, ask_manual_calories)
		return

	df = load_users()
	df.loc[df.user_id == message.chat.id, "logged_calories"] += calories
	save_df_to_s3(df, CSV_FILE)

	append_food_log(message.chat.id, calories)

	bot.send_message(message.chat.id, f"✅ Записано вручную: {calories} ккал")

@bot.message_handler(commands=["check_progress"])
@log_message
def check_progress(message):
	reset_daily_if_needed(message.chat.id)
	df = load_users()
	if df.empty or message.chat.id not in df['user_id'].values:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	user_local = df[df.user_id == message.chat.id].iloc[0]

	water_logged = float(user_local.logged_water)
	water_goal = float(user_local.water_goal)
	water_left = max(water_goal - water_logged, 0)

	calories_logged = float(user_local.logged_calories)
	calorie_goal = float(user_local.calorie_goal)
	calories_left = max(calorie_goal - calories_logged, 0)

	burned = float(user_local.burned_calories)

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


@bot.message_handler(commands=["profile"])
@log_message
def profile(message):
	reset_daily_if_needed(message.chat.id)
	df = load_users()
	if df.empty or message.chat.id not in df['user_id'].values:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	user_local = df[df.user_id == message.chat.id].iloc[0]
	user_tg = message.from_user
	if user_local.gender == "m":
		gender_send = "Мужской"
	else:
		gender_send = "Женский"
	bot.send_message(
		message.chat.id,
		f"Информация о {user_tg.first_name}\n"
		f"📋 Пол: {gender_send}\n"
		f"⚖️ Вес: {user_local.weight} кг\n"
		f"📏 Рост: {user_local.height} см\n"
		f"🎂 Возраст: {user_local.age} лет\n"
		f"🏃 Активность: {user_local.activity} мин/день\n"
		f"🏙️ Город: {user_local.city}"
	)


@bot.message_handler(commands=["stats"])
@log_message
def stats(message):
	user_id = message.chat.id
	today_start = datetime.combine(date.today(), time.min)

	users_df = load_users()
	if users_df.empty or user_id not in users_df['user_id'].values:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	water_goal = float(users_df.loc[users_df.user_id == user_id, "water_goal"].iloc[0])
	calorie_goal = float(users_df.loc[users_df.user_id == user_id, "calorie_goal"].iloc[0])

	# График по воде
	water_df = load_df_from_s3(WATER_LOG_CSV)
	if not water_df.empty:
		water_df["datetime"] = pd.to_datetime(water_df["datetime"])
		water_df = water_df[
			(water_df.user_id == user_id) &
			(water_df.datetime >= today_start)
		]
		
		if not water_df.empty:
			water_df["step"] = range(1, len(water_df) + 1)
			water_df["cumulative"] = water_df.amount_ml.cumsum()
			
			def plot_water():
				plt.figure(figsize=(10, 6))
				plt.plot(
					water_df["step"],
					water_df["cumulative"],
					marker="o",
					linewidth=2
				)
				plt.axhline(water_goal, color='r', linestyle="--", label=f'Цель: {water_goal} мл')
				plt.title("Прогресс выпитой воды за день")
				plt.xticks(water_df["step"])
				plt.xlabel("Приёмы воды")
				plt.ylabel("мл")
				plt.legend()
				plt.grid(True, alpha=0.3)
				plt.tight_layout()
			
			send_plot_as_photo(message.chat.id, plot_water)
		else:
			bot.send_message(message.chat.id, "За сегодня нет записей о воде")

	# График по калориям
	food_df = load_df_from_s3(FOOD_LOG_CSV)
	if not food_df.empty:
		food_df["datetime"] = pd.to_datetime(food_df["datetime"])
		food_df = food_df[
			(food_df.user_id == user_id) &
			(food_df.datetime >= today_start)
		]
		
		if not food_df.empty:
			food_df["step"] = range(1, len(food_df) + 1)
			food_df["cumulative"] = food_df.calories.cumsum()
			
			def plot_food():
				plt.figure(figsize=(10, 6))
				plt.plot(
					food_df["step"],
					food_df["cumulative"],
					marker="o",
					linewidth=2
				)
				plt.axhline(calorie_goal, color='r', linestyle="--", label=f'Цель: {calorie_goal} ккал')
				plt.title("Прогресс по калориям за день")
				plt.xticks(food_df["step"])
				plt.xlabel("Приёмы еды")
				plt.ylabel("ккал")
				plt.legend()
				plt.grid(True, alpha=0.3)
				plt.tight_layout()
			
			send_plot_as_photo(message.chat.id, plot_food)
		else:
			bot.send_message(message.chat.id, "За сегодня нет записей о еде")

@bot.message_handler(commands=["tip"])
@log_message
def tip(message):
	reset_daily_if_needed(message.chat.id)

	df = load_users()
	if df.empty or message.chat.id not in df['user_id'].values:
		bot.send_message(message.chat.id, "Сначала заполните профиль: /set_profile")
		return

	user_local = df[df.user_id == message.chat.id].iloc[0]

	calories_logged = float(user_local.logged_calories)
	calorie_goal = float(user_local.calorie_goal)

	delta = calorie_goal - calories_logged

	# Когда осталось место в ежедневной норме калорий
	if delta > 0:
		food_df = load_df_from_s3(HEALTH_FOOD_CSV)
		if food_df.empty:
			bot.send_message(message.chat.id, "База здоровых продуктов недоступна")
			return
		
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
		burn_rate = 350  # Сжигаемые калории за час тренировки
		activity = "🚶‍♂️ Быстрая ходьба"
	else:
		burn_rate = 680
		activity = "🏃‍♂️ Бег"

	minutes = int((excess / burn_rate) * 60)
	minutes = min(minutes, 90)

	bot.send_message(
		message.chat.id,
		f"🔥 Вы превысили норму на {int(excess)} ккал\n"
		f"{activity}\n"
		f"⏱ Рекомендуемое время: {minutes} минут"
	)

def handler(event, context):
	try:
		if event.get("httpMethod") == "POST":
			body = event.get('body', '')
			if not body:
				return {'statusCode': 400, 'body': 'Empty body'}

			update_dict = json.loads(body)
			update = telebot.types.Update.de_json(update_dict)
			bot.process_new_updates([update])
			return {
				'statusCode': 200,
				'body': json.dumps({'status': 'OK'})
			}
		else:
			return {
				'statusCode': 200,
				'body': 'Webhook active'
			}

	except Exception as e:
		return {
			'statusCode': 500,
			'body': json.dumps({'error': str(e)})
		}