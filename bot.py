
import os
import discord
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import json
from datetime import datetime, timedelta
import re
from difflib import get_close_matches
from discord.ext.commands import MissingRequiredArgument
import asyncio
import logging
from dotenv import load_dotenv


# Setup logging for better debugging and monitoring
logging.basicConfig(level=logging.INFO)

# Load environment variables from .env
load_dotenv()


# Set up intents (this determines what events the bot can listen for)
intents = discord.Intents.default()
intents.message_content = True  # Make sure the bot can read messages

# Discord bot setup with intents
bot = commands.Bot(command_prefix="!", intents=intents)

# Load sensitive data from environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
if not DISCORD_TOKEN:
    logging.error("Error: DISCORD_TOKEN is not set!")
CLAUDE_API_KEY = os.getenv('CLAUDE_API_KEY')
if not CLAUDE_API_KEY:
    logging.error("Error: CLAUDE_API_KEY is not set!")
GOOGLE_SHEETS_CREDS = os.getenv('GOOGLE_SHEETS_CREDS')
if not GOOGLE_SHEETS_CREDS:
    logging.error("Error: GOOGLE_SHEETS_CREDS is not set!")

# Claude API configuration
CLAUDE_API_URL = 'https://api.anthropic.com/v1/messages'

# Function to interact with Claude API
def ask_claude(prompt, model="claude-3-haiku-20240307"):
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    
    data = {
        "model": model,
        "max_tokens": 150,  # Limit the response to 150 tokens
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
    
    # Make the API request
    response = requests.post(CLAUDE_API_URL, headers=headers, data=json.dumps(data))
    
    if response.status_code == 200:
        result = response.json()
        print("Claude API Full Response:", result)  # Debug: Print the full response for logging
        
        # Extract text from the 'content' field in the response
        if 'content' in result and isinstance(result['content'], list) and 'text' in result['content'][0]:
            return result['content'][0]['text']  # Extract the text response from Claude
        else:
            return "Sorry, I couldn't process that."
    else:
        return "Error with Claude API: " + response.text


# Google Sheets setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
# Load the credentials file name from the environment variable
credentials_file = os.getenv('GOOGLE_SHEETS_CREDS')  # This should already be set in your .env
if credentials_file:
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file, scope)  
    client = gspread.authorize(creds)
else:
    logging.error("Error: GOOGLE_SHEETS_CREDS is not set!")

# Open the Google Sheet by URL
GOOGLE_SHEETS_URL = os.getenv('GOOGLE_SHEETS_URL')
sheet = client.open_by_url(GOOGLE_SHEETS_URL)
worksheet = sheet.sheet1  # Open the first worksheet (sheet1)

# Function to extract transaction details (with better validation and error handling)
def extract_transaction_details(claude_response):
    try:
        amount_match = re.search(r'\$\d{1,3}(,\d{3})*(\.\d{2})?', claude_response)
        date_match = re.search(r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s\d{2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s\d{4}', claude_response)
        
        amount = float(amount_match.group().replace('$', '').replace(',', '')) if amount_match else None
        payment_date = datetime.strptime(date_match.group(), '%a %d %b %Y').strftime('%Y-%m-%d') if date_match else None
        cover_date = (datetime.strptime(payment_date, '%Y-%m-%d') - timedelta(days=14)).strftime('%Y-%m-%d') if payment_date else None
        
        return payment_date, cover_date, amount
    except Exception as e:
        logging.error(f"Error extracting transaction details: {str(e)}")
        return None, None, None

# Function to log payment to Google Sheets with a serial number
def log_payment_to_sheet(serial_number, user, payment_date, amount, log_date, cover_date, next_rent_date):
    try:
        worksheet.append_row([
            serial_number, 
            user, 
            payment_date.strftime('%Y-%m-%d'), 
            f"${amount:.2f}", 
            log_date.strftime('%Y-%m-%d'), 
            cover_date.strftime('%Y-%m-%d'), 
            next_rent_date.strftime('%Y-%m-%d')
        ])
        return f"Logged payment of ${amount} for rent on {payment_date.strftime('%Y-%m-%d')} from {user}. Serial number: {serial_number}."
    except Exception as e:
        logging.error(f"Failed to log payment: {str(e)}")
        return "Failed to log payment due to an error."
    
#change usernames
@bot.command()
async def update_names(ctx):
    try:
        # Fetch all records from the sheet
        all_records = worksheet.get_all_records()

        # Iterate through each record and check for the user to replace
        for i, record in enumerate(all_records, start=2):  # Start from 2 because row 1 is the header
            if record['Paid By'] == "heheboi_2024":
                worksheet.update_cell(i, 2, "SonamKhadka")  # Update the 'Paid By' column to "SonamKhadka"
            elif record['Paid By'] == "siru0785":
                worksheet.update_cell(i, 2, "SrijanaKattel")  # Update the 'Paid By' column to "SrijanaKattel"

        await ctx.send("Usernames updated successfully.")

    except Exception as e:
        await ctx.send(f"Error updating names: {str(e)}")


# Command to log rent payments with optional manual payment date
@bot.command()
async def log_payment(ctx, amount: float = None, payment_date: str = None):
    if amount is None:
        await ctx.send("The `amount` argument is required. Please provide the amount like `!log_payment 100.0`.")
        return

    try:
        user_id = str(ctx.author.id)
        user_name = str(ctx.author)

        # Get the current date as the log date
        log_date = datetime.now()

        # Parse payment date or use current date
        if payment_date:
            try:
                payment_date = datetime.strptime(payment_date, '%d/%m/%Y')
            except ValueError:
                await ctx.send("Invalid date format. Please use DD/MM/YYYY.")
                return
        else:
            payment_date = log_date

        # Get all receipts to determine the next serial number
        all_receipts = worksheet.get_all_records()
        serial_number = len(all_receipts) + 1

        # Calculate the next due date and cover date
        next_due_date = (payment_date + timedelta(days=14))
        cover_date = (payment_date - timedelta(days=14))

        # Log the payment
        log_message = log_payment_to_sheet(serial_number, user_name, payment_date, amount, log_date, cover_date, next_due_date)
        
        await ctx.send(log_message)
        await ctx.send(f"Your next payment is due on {next_due_date.strftime('%d/%m/%Y')}.")
    except Exception as e:
        logging.error(f"Error in log_payment: {str(e)}")
        await ctx.send(f"Error: {str(e)}")


        
# Command to show specific payment logs
@bot.command()
async def show_receipt(ctx, identifier: str):
    try:
        # Get all rows, skipping the header
        all_rows = worksheet.get_all_values()[1:]

        SERIAL_NUMBER_COL = 0
        PAYMENT_DATE_COL = 2

        # Attempt to treat the identifier as a serial number
        try:
            serial_number = int(identifier)
            for row in all_rows:
                if row[SERIAL_NUMBER_COL] == str(serial_number):
                    await ctx.send(f"Receipt for {row[1]}: Serial Number: {serial_number}, Payment Date: {row[PAYMENT_DATE_COL]}, Amount: {row[3]}")
                    return
            await ctx.send(f"No receipt found with serial number {serial_number}.")
        
        except ValueError:
            try:
                payment_date = datetime.strptime(identifier, '%d/%m/%Y').strftime('%Y-%m-%d')
            except ValueError:
                await ctx.send("Invalid input. Please provide a valid serial number or a date in DD/MM/YYYY format.")
                return

            for row in all_rows:
                if row[PAYMENT_DATE_COL] == payment_date:
                    await ctx.send(f"Receipt for {row[1]}: Serial Number: {row[0]}, Payment Date: {row[PAYMENT_DATE_COL]}, Amount: {row[3]}")
                    return
            await ctx.send(f"No receipt found for {payment_date}.")
    
    except Exception as e:
        logging.error(f"Error in show_receipt: {str(e)}")
        await ctx.send(f"Error: {str(e)}")



#edit receipts 
@bot.command()
async def edit_receipt(ctx, identifier: str, new_amount: float):
    try:
        # Try to treat the identifier as a serial number first
        try:
            serial_number = int(identifier)
            cell = worksheet.find(str(serial_number))
            
            if cell:
                worksheet.update_cell(cell.row, 4, new_amount)  # Assuming column 4 is the amount
                await ctx.send(f"Receipt with serial number {serial_number} updated to new amount: ${new_amount}")
            else:
                await ctx.send(f"No receipt found with serial number {serial_number}.")
        
        except ValueError:
            # If it's not a serial number, treat it as a date
            try:
                payment_date = datetime.strptime(identifier, '%d/%m/%Y').strftime('%Y-%m-%d')
            except ValueError:
                await ctx.send("Invalid input. Please provide either a valid serial number or a date in the format DD/MM/YYYY.")
                return

            cell = worksheet.find(payment_date)
            if cell:
                worksheet.update_cell(cell.row, 4, new_amount)
                await ctx.send(f"Receipt for {payment_date} updated to new amount: ${new_amount}")
            else:
                await ctx.send(f"No receipt found for {payment_date}.")
    except Exception as e:
        await ctx.send(f"Error: {str(e)}")


@bot.command(name="show_detailed_receipt")
async def show_detailed_receipt(ctx, identifier: str = None):
    # If no identifier is provided, ask the user to provide one
    if identifier is None:
        await ctx.send("Please provide a serial number or a date in the format DD/MM/YYYY. For example: `!show_receipt 12/02/2024`.")
        return
    
    # Now, proceed to check the payment logs by serial number or date
    try:
        # Try to treat the identifier as a serial number first
        try:
            serial_number = int(identifier)
            cell = worksheet.find(str(serial_number))
            
            if cell:
                row = worksheet.row_values(cell.row)
                serial_number, payment_date, cover_date, amount, user = row
                await ctx.send(f"Receipt for {user}: Serial Number: {serial_number}, Payment Date: {payment_date}, Cover Date: {cover_date}, Amount: ${amount}")
            else:
                await ctx.send(f"No receipt found with serial number {serial_number}.")
        
        except ValueError:
            # If it's not a serial number, treat it as a date
            try:
                payment_date = datetime.strptime(identifier, '%d/%m/%Y').strftime('%Y-%m-%d')
            except ValueError:
                await ctx.send("Invalid input. Please provide either a valid serial number or a date in the format DD/MM/YYYY.")
                return

            cell = worksheet.find(payment_date)
            if cell:
                row = worksheet.row_values(cell.row)
                user, payment_date, cover_date, amount = row
                await ctx.send(f"Receipt for {user}: Payment Date: {payment_date}, Cover Date: {cover_date}, Amount: ${amount}")
            else:
                await ctx.send(f"No receipt found for {payment_date}.")
    
    except Exception as e:
        await ctx.send(f"Error: {str(e)}")



# Command to delete a specific payment entry
@bot.command()
async def delete_receipt(ctx, identifier: str):
    try:
        # First, try to treat the identifier as a serial number
        try:
            serial_number = int(identifier)
            # Find the row that contains the serial number
            cell = worksheet.find(str(serial_number))  # Assuming serial number is stored as a string
            
            if cell:
                worksheet.delete_rows(cell.row)
                await ctx.send(f"Receipt with serial number {serial_number} deleted successfully.")
            else:
                await ctx.send(f"No receipt found with serial number {serial_number}.")
        
        except ValueError:
            # If it's not a serial number, treat it as a date
            try:
                payment_date = datetime.strptime(identifier, '%d/%m/%Y').strftime('%Y-%m-%d')
            except ValueError:
                await ctx.send("Invalid input. Please provide either a valid serial number or a date in the format DD/MM/YYYY.")
                return

            # Find the row that contains the payment date
            cell = worksheet.find(payment_date)

            if cell:
                worksheet.delete_rows(cell.row)
                await ctx.send(f"Receipt for {payment_date} deleted successfully.")
            else:
                await ctx.send(f"No receipt found for {payment_date}.")
    
    except Exception as e:
        await ctx.send(f"Error: {str(e)}")




def ask_claude_with_bot_integration(question, bot_commands_prompt):
    # Combine the question with the bot's command capabilities
    prompt = f"{bot_commands_prompt}\n\nUser Question: {question}"
    
    # Send this to Claude
    return ask_claude(prompt)
        
@bot.command()
async def ask_ai(ctx, *, question: str):
    # Create a dynamic bot commands prompt for Claude
    bot_commands_prompt = """
    Claude, you are working with a payment tracking bot. It responds to the following commands:
    - `!show_receipt <date>`: Use this to check if a payment was made on a specific date.
    - `!log_payment <amount>`: Use this to log a payment with a specific amount.
    - `!delete_receipt <date>`: Use this to delete a payment record for a specific date.

    When someone asks you to check a payment, if it matches a bot command, trigger the appropriate bot command and return the result.
    """

    # Check if the question contains a date (e.g., 12/02/2024)
    date_match = re.search(r'(\d{2}/\d{2}/\d{4})', question)  # Extract date in DD/MM/YYYY format
    if date_match:
        date = date_match.group(1)
        # Check if payment exists in logs
        try:
            cell = worksheet.find(date)
            if cell:
                row = worksheet.row_values(cell.row)
                user, payment_date, cover_date, amount = row
                await ctx.send(f"Yes, a payment of ${amount} was logged for {user} on {payment_date}.")
            else:
                # Ask Claude if no entry is found, include bot commands prompt
                response = ask_claude_with_bot_integration(question, bot_commands_prompt)
                if response:  # Check if Claude's response is not empty
                    await ctx.send(response)
                else:
                    await ctx.send("I couldn't find any relevant information.")
        except Exception as e:
            await ctx.send(f"Error while checking logs: {str(e)}")
    else:
        try:
            # Fallback to Claude if no date was found, include bot commands prompt
            response = ask_claude_with_bot_integration(question, bot_commands_prompt)
            if response:
                await ctx.send(response)
            else:
                await ctx.send("I couldn't find any relevant information.")
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

def ask_claude_with_bot_integration(question, bot_commands_prompt):
    # Combine the question with the bot's command capabilities
    prompt = f"{bot_commands_prompt}\n\nUser Question: {question}"
    
    # Send this to Claude using your existing Claude function
    response = ask_claude(prompt)
    
    # Check if Claude suggests a bot command like `!show_receipt`
    if "!show_receipt" in response:
        # Extract the date and execute the command internally
        date_match = re.search(r'!show_receipt (\d{2}/\d{2}/\d{4})', response)
        if date_match:
            date = date_match.group(1)
            # Now call your bot's internal show_receipt function
            return show_receipt(date)
    
    # If no command is found, return Claude's response
    return response

@bot.command(name="show_receipt_details")
async def show_receipt_details(ctx, payment_date: str = None):
    # If no date is provided, ask the user to provide one
    if payment_date is None:
        await ctx.send("Please provide a date in the format DD/MM/YYYY. For example: `!show_receipt 12/02/2024`.")
        return
    
    # Now, proceed to check the payment logs
    try:
        cell = worksheet.find(payment_date)
        if cell:
            row = worksheet.row_values(cell.row)
            user, payment_date, cover_date, amount = row
            await ctx.send(f"Receipt for {user}: Payment Date: {payment_date}, Cover Date: {cover_date}, Amount: ${amount}")
        else:
            await ctx.send(f"No receipt found for {payment_date}.")
    except Exception as e:
        await ctx.send(f"Error: {str(e)}")

@bot.command()
async def help_command(ctx):
    help_message = """
    Available commands:
    - `!log_payment <amount>`: Log a payment with a specific amount.
    - `!show_receipt <date>`: Check if a payment was made on a specific date.
    - `!delete_receipt <date>`: Delete a payment record for a specific date.
    - `!request_report [channel/dm]`: Request a payment report. Send it to a channel or as a DM.
    - `!show_all_receipts`: Show all receipts logged in the system.
    - `!show_receipts_range <start_date> <end_date>`: Show receipts within a date range.
    - `!start_reminder`: Start reminders for upcoming payments.
    
    Example usage:
    - `!log_payment 100.0`: Logs a payment of $100.
    - `!show_receipt 12/02/2024`: Shows the receipt for 12/02/2024.
    - `!delete_receipt 12/02/2024`: Deletes the receipt for 12/02/2024.
    - `!request_report channel`: Requests a report sent to the channel.
    - `!show_receipts_range 01/09/2024 15/09/2024`: Shows receipts between 01/09/2024 and 15/09/2024.
    - `!start_reminder`: Start reminders after the due date for fortnightly rent payments.
    """
    await ctx.send(help_message)


VALID_COMMANDS = ["!log_payment", "!show_receipt", "!delete_receipt", "!help_command"]

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        # Handle command not found and suggest a valid command
        invalid_command = ctx.message.content.split()[0]
        closest_matches = get_close_matches(invalid_command, VALID_COMMANDS, n=1, cutoff=0.6)
        
        if closest_matches:
            await ctx.send(f"Did you mean `{closest_matches[0]}`?")
        else:
            await ctx.send("Invalid command. Type `!help_command` to see available commands.")
    
    elif isinstance(error, MissingRequiredArgument):
        # Handle missing required arguments for different commands
        if error.param.name == 'payment_date':
            await ctx.send("The `payment_date` argument is required. Please provide a date in the format DD/MM/YYYY.")
        elif error.param.name == 'amount':
            await ctx.send("The `amount` argument is required. Please provide the amount like `!log_payment 100.0`.")
        else:
            await ctx.send(f"The argument `{error.param.name}` is missing. Please provide the required information.")
    
    else:
        # Re-raise other errors if necessary
        raise error
@bot.command()
async def show_receipts_range(ctx, start_date: str, end_date: str):
    try:
        # Convert start and end dates to datetime objects
        start_date_dt = datetime.strptime(start_date, '%d/%m/%Y')
        end_date_dt = datetime.strptime(end_date, '%d/%m/%Y')
        
        all_receipts = worksheet.get_all_records()  # Retrieve all records
        filtered_receipts = []

        # Filter receipts based on the date range
        for receipt in all_receipts:
            receipt_date_dt = datetime.strptime(receipt['payment_date'], '%Y-%m-%d')
            if start_date_dt <= receipt_date_dt <= end_date_dt:
                filtered_receipts.append(receipt)

        if filtered_receipts:
            receipt_message = f"Receipts from {start_date} to {end_date}:\n"
            for receipt in filtered_receipts:
                receipt_message += f"User: {receipt['user']}, Payment Date: {receipt['payment_date']}, Amount: {receipt['amount']}\n"
            await ctx.send(receipt_message)
        else:
            await ctx.send(f"No receipts found between {start_date} and {end_date}.")
    except Exception as e:
        await ctx.send(f"Error: {str(e)}")
# Starting due date (20/09/2024)
initial_due_date = datetime.strptime('20/09/2024', '%d/%m/%Y')

# Calculate the next due date
def get_next_due_date(last_payment_date=None):
    if last_payment_date:
        # Add 14 days to the last payment date to get the next due date
        return last_payment_date + timedelta(days=14)
    else:
        # If no last payment date, return the initial due date
        return initial_due_date
    
# Dictionary to track whether a user has logged their payment
user_payment_logged = {}
    
def is_payment_logged():
    all_receipts = worksheet.get_all_records()
    print(all_receipts[0])
    today = datetime.now().strftime('%Y-%m-%d')

    # Check if there's any payment logged for the current period
    for receipt in all_receipts:
        payment_date = receipt.get('Payment Date')  # Use .get() to avoid KeyError
        if payment_date == today:
            return True  # Payment has been logged today
    return False  # No payment logged for today

# Function to send a reminder to the rent reminder channel
async def send_fortnightly_reminder(channel):
    due_date = get_next_due_date()  # Start with the initial due date
    while True:
        # Check if the payment has been logged
        if is_payment_logged():
            await channel.send(f"Thank you! The rent payment has been logged.")
            break  # Stop reminding once payment is logged
        else:
            # If today is past the due date, send a reminder
            if datetime.now() > due_date:
                await channel.send(f"Reminder: Rent payment is overdue. Please log your payment using `!log_payment <amount>`.")
            else:
                await channel.send(f"Reminder: Your next rent payment is due on {due_date.strftime('%d/%m/%Y')}.")

        # Wait 24 hours before sending the next reminder
        await asyncio.sleep(86400)  # Remind every 24 hours

# Example of the send_fortnightly_report function
async def send_fortnightly_report():
    while True:
        # Fetch all receipts from Google Sheets or database
        all_receipts = worksheet.get_all_records()
        today = datetime.now()

        # Generate a report for the past two weeks
        start_date = (today - timedelta(weeks=2)).strftime('%Y-%m-%d')
        report_message = f"Fortnightly Report (from {start_date} to {today.strftime('%Y-%m-%d')}):\n"
        total_amount = 0

        # Iterate through the receipts and filter them by the date range
        for receipt in all_receipts:
            receipt_date = receipt['Payment Date']
            if start_date <= receipt_date <= today.strftime('%Y-%m-%d'):
                report_message += f"User: {receipt['Paid By']}, Date: {receipt_date}, Amount: {receipt['Amount']}\n"
                total_amount += float(receipt['Amount'].replace('$', '').replace(',', ''))  # Convert amount to a float

        report_message += f"\nTotal amount paid: ${total_amount}"

        # Fetch the report channel from the ID stored in .env for reports and requests
        REPORT_CHANNEL_ID = int(os.getenv('REPORT_CHANNEL_ID'))  # Ensure the ID is in .env
        report_channel = bot.get_channel(REPORT_CHANNEL_ID)

        # Ensure the channel was found before sending the message
        if report_channel:
            await report_channel.send(report_message)
        else:
            print("Error: Report channel not found or invalid ID")

        # Wait for two weeks (1209600 seconds) before sending the next report
        await asyncio.sleep(1209600)


@bot.command()
async def request_report(ctx, destination: str = "channel"):
    try:
        all_receipts = worksheet.get_all_records()  # Fetch all receipts
        today = datetime.now()

        # Generate the report for the past two weeks
        start_date = (today - timedelta(weeks=2)).strftime('%Y-%m-%d')
        report_message = f"Requested Report (from {start_date} to {today.strftime('%Y-%m-%d')}):\n"
        total_amount = 0

        for receipt in all_receipts:
            receipt_date = receipt['payment_date']
            if start_date <= receipt_date <= today.strftime('%Y-%m-%d'):
                report_message += f"User: {receipt['user']}, Date: {receipt_date}, Amount: {receipt['amount']}\n"
                total_amount += float(receipt['amount'])

        report_message += f"\nTotal rent paid: ${total_amount}"

        # If the user wants the report in their DMs
        if destination.lower() == "dm":
            await ctx.author.send(report_message)
        else:
            # Fetch the report channel from the ID stored in .env for reports and requests
            REPORT_CHANNEL_ID = int(os.getenv('REPORT_CHANNEL_ID'))  # Ensure the ID is in .env
            report_channel = bot.get_channel(REPORT_CHANNEL_ID)

            # Ensure the channel was found before sending the message
            if report_channel:
                await report_channel.send(report_message)
            else:
                print("Error: Report channel not found or invalid ID")
                
    except Exception as e:
        await ctx.send(f"Error generating report: {str(e)}")
        
async def send_trash_reminders():
    # Get the channel ID from the .env file
    channel_id = int(os.getenv("TRASH_REMINDER_CHANNEL_ID"))
    channel = bot.get_channel(channel_id)

    if channel:
        print(f"Bot found the channel: {channel.name}")
    else:
        print(f"Channel with ID {channel_id} not found.")

    # Reminder times in hours (24-hour format)
    reminder_times = [20, 22, 0]  # 8 PM, 10 PM, and 12 AM (midnight)

    while True:
        now = datetime.now()

        # Debugging: Print the current date and time
        print(f"Current Date and Time: {now.strftime('%Y-%m-%d %H:%M')}")

        # Check if today is Thursday (weekday() returns 3 for Thursday)
        if now.weekday() == 3:  # 3 represents Thursday
            print("It's Thursday!")  # Debugging: Check if it correctly identifies Thursday
            for reminder_time in reminder_times:
                # Check if the time matches a reminder time, within a 1-minute window
                if now.hour == reminder_time and 0 <= now.minute < 2:  # Looser time window for reminders
                    print(f"Sending reminder for {reminder_time}:00!")
                    if channel:
                        await channel.send(f"Reminder: Take the trash or bin out! It's {now.strftime('%I:%M %p')} on Thursday.")
                    else:
                        print(f"Channel with ID {channel_id} not found.")
                
                # Wait for 60 seconds before checking again
                await asyncio.sleep(60)
        else:
            print("It's not Thursday yet.")
            # Wait for an hour if it's not Thursday
            await asyncio.sleep(3600)


@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")
    
    # Get the rent reminder channel from the .env file
    rent_reminder_channel = bot.get_channel(int(os.getenv('RENT_REMINDER_CHANNEL_ID')))
    
    # Start rent reminder task for the rent reminder channel
    if rent_reminder_channel:
        bot.loop.create_task(send_fortnightly_reminder(rent_reminder_channel))
    else:
        print("Error: Rent reminder channel not found.")

    # Start fortnightly report task
    bot.loop.create_task(send_fortnightly_report())

# Run the bot (retrieve the bot token from environment variables)
bot.run(DISCORD_TOKEN)
