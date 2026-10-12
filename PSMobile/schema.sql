
-- Create Products Table
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    brand TEXT,
    model TEXT,
    category TEXT,
    current_stock INTEGER DEFAULT 0,
    image_url TEXT,
    is_archived BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create Stock Movements Table
CREATE TABLE stock_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    quantity INTEGER NOT NULL,
    type TEXT CHECK (type IN ('IN', 'OUT')),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for faster searching
CREATE INDEX idx_products_code ON products(product_code);
CREATE INDEX idx_products_name ON products(name);
CREATE INDEX idx_movements_product ON stock_movements(product_id);
