-- Migration script for data retention feature
-- Creates market_data_summary table for aggregated data

-- Create market_data_summary table
CREATE TABLE IF NOT EXISTS bdo_marketdatasummary (
    id SERIAL PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES bdo_item(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    avg_last_sold_price DECIMAL(15, 2) NOT NULL,
    min_last_sold_price BIGINT NOT NULL,
    max_last_sold_price BIGINT NOT NULL,
    avg_current_stock DECIMAL(15, 2) NOT NULL,
    total_trades_sum BIGINT NOT NULL,
    record_count INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE (item_id, date)
);

-- Create index for efficient querying
CREATE INDEX IF NOT EXISTS idx_summary_item_date 
ON bdo_marketdatasummary (item_id, date DESC);

-- Create index for date-based queries
CREATE INDEX IF NOT EXISTS idx_summary_date 
ON bdo_marketdatasummary (date);

-- Add comments for documentation
COMMENT ON TABLE bdo_marketdatasummary IS 'Daily aggregated summaries of market data for long-term retention';
COMMENT ON COLUMN bdo_marketdatasummary.avg_last_sold_price IS 'Average last sold price for the day';
COMMENT ON COLUMN bdo_marketdatasummary.min_last_sold_price IS 'Minimum last sold price for the day';
COMMENT ON COLUMN bdo_marketdatasummary.max_last_sold_price IS 'Maximum last sold price for the day';
COMMENT ON COLUMN bdo_marketdatasummary.avg_current_stock IS 'Average current stock for the day';
COMMENT ON COLUMN bdo_marketdatasummary.total_trades_sum IS 'Sum of total trades for the day';
COMMENT ON COLUMN bdo_marketdatasummary.record_count IS 'Number of detailed records aggregated into this summary';
