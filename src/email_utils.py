import resend

def send_email_report(subject, html_content, recipient, api_key):
    """
    A 'Dumb' Sender. 
    It takes instructions from niche_hunter.py and executes them.
    
    Args:
        subject (str): The email subject.
        html_content (str): The full HTML report.
        recipient (str): Who to send it to.
        api_key (str): The specific Resend Key to use.
    """
    
    if not api_key:
        print(f"❌ Error: No API Key provided for {recipient}. Email skipped.")
        return

    try:
        # 1. Set the specific key for this user
        resend.api_key = api_key

        # 2. Build the email package
        params = {
            "from": "PrimoGreedy <onboarding@resend.dev>",
            "to": [recipient],
            "subject": subject,
            "html": html_content
        }

        # 3. Send it
        r = resend.Emails.send(params)
        print(f"✅ Email successfully sent to {recipient} (ID: {r.get('id')})")
        return r

    except Exception as e:
        print(f"❌ Failed to send to {recipient}: {str(e)}")
        # We don't raise the error so the loop keeps going for other users
        return None