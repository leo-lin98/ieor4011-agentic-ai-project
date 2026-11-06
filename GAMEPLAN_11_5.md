Api guide:
https://docs.kalshi.com/welcome
https://docs.polymarket.us/getting-started/quickstart

We are going to be building out an agentic system that allows us to compare identical market. These are the metrics that I am interested in: 

Event_Ticker.Kalshi
Event_Title.Kalshi
Yes_Title_{i}.Kalshi 
No_Title_{i}.Kalshi
Yes_Price_{i}.Kalshi (Note: Price must be corresponding to the Yes_Title_{i} as there are multiple yes/no prices in certain markets)
No_Price_{i}.Kalshi (Note: Price must be corresponding to the No_Title_{i} as there are multiple yes/no prices in certain markets)
Market_Open_Date.Kalshi
Market_Close_Date.Kalshi
Time_Until_Close.Kalshi
Market_About_Section.Kalshi
Market_Rules_Section.Kalshi
Time_snapshot.Kalshi (return the timestamp of the data pull, we will pull market data every 60 seconds)
Market_URL.Kalshi
Market_Status.Kalshi (Open, Settled, Closed)
Market_Total_Volume.Kalshi 


Event_Ticker.Polymarket
Event_Title.Polymarket
Yes_Title_{i}.Polymarket
No_Title_{i}.Polymarket
Yes_Price_{i}.Polymarket (Note: Price must be corresponding to the Yes_Title_{i} as there are multiple yes/no prices in certain markets)
No_Price_{i}.Polymarket (Note: Price must be corresponding to the No_Title_{i} as there are multiple yes/no prices in certain markets)
Market_Open_Date.Polymarket
Market_Close_Date.Polymarket
Time_Until_Close.Polymarket
Market_About_Section.Polymarket
Market_Rules_Section.Polymarket
Time_snapshot.Polymarket (return the timestamp of the data pull, we will pull market data every 60 seconds)
Market_URL.Polymarket
Market_Status.Polymarket (Open, Settled, Closed)
Market_Total_Volume.Polymarket 


Note that all of the timestamps must be in UTC, and the prices must be in cents (0-100). We want to standardize the data pull so ensure the timestamps are the same for both sources. This step is a massive data collection since neither polymarket nor Kalshi track closed markets

I need you to track in a different section, the total number of markets that are open on both platforms.