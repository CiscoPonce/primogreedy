from src.finance_tools import get_insider_sentiment, get_company_news, get_basic_financials

print('--- INSIDER ---')
print(get_insider_sentiment.invoke({'ticker': 'AAPL'}))

print('\n--- NEWS ---')
print(get_company_news.invoke({'ticker': 'AAPL'}))

print('\n--- FINANCIALS ---')
print(get_basic_financials.invoke({'ticker': 'AAPL'}))
