# Supply Chain Health Guide

## Overview
Supply chain reliability is foundational to vendor performance. In-stock rate directly impacts Revenue, CR, and Glance Views. This guide covers supply chain optimization strategies for Amazon vendors.

## Key Supply Chain Metrics

### In-Stock Rate
- **Target**: > 97% for top 50 ASINs, > 95% overall
- **Impact**: Every 1% in-stock rate improvement drives ~0.7% revenue lift
- **Measurement**: Calculated as (available hours / total hours) per ASIN, weighted by revenue

### Fill Rate
- **Target**: > 98%
- **Definition**: Percentage of purchase order units fulfilled as requested
- **Impact**: Low fill rate damages vendor scorecard and may lead to chargebacks

### Lead Time
- **Target**: < 30 days for domestic, < 60 days for international
- **Definition**: Time from PO acknowledgment to Amazon warehouse receipt
- **Impact**: Longer lead times require higher safety stock and increase stock-out risk

## Supply Chain Optimization Strategies

### Demand Forecasting Improvement
1. **Use Amazon Demand Forecasting Tool**: Available in Vendor Central, provides 4-week forward forecast
2. **Account for seasonality**: Build seasonality factors into inventory planning
3. **Track forecast accuracy**: Measure MAPE (Mean Absolute Percentage Error), target < 25%
4. **Align with promotional calendar**: Pre-build inventory for deals and seasonal peaks
5. **Monitor category trends**: Track category-level demand signals for early warning

### Lead Time Reduction
1. **Domestic sourcing**: Evaluate domestic manufacturing for top ASINs to reduce lead time
2. **Pre-positioning**: Store inventory closer to Amazon fulfillment centers
3. **PO processing automation**: Automate PO acknowledgment within 4 hours
4. **Shipping optimization**: Use Amazon Preferred Carrier program for faster transit
5. **Container optimization**: Maximize container fill rate to reduce per-unit shipping cost

### Safety Stock Management
1. **Calculate safety stock**: SS = Z-score x standard deviation of demand x sqrt(lead time)
2. **Tier by ASIN importance**: Higher safety stock for top-revenue and high-margin ASINs
3. **Dynamic adjustment**: Increase safety stock during seasonal peaks and promotions
4. **Review monthly**: Adjust based on demand variability changes

## When In-Stock Rate is Below Goal

### Immediate Actions (0-48 hours)
1. Identify out-of-stock ASINs and check pending PO status
2. For critical ASINs (top 10 revenue), expedite shipping if inventory available
3. Check for PO compliance issues - missed or late shipments
4. Communicate with Amazon Vendor Support if PO delays are Amazon-side

### Short-term Actions (1-2 weeks)
1. Conduct root cause analysis: forecast miss, lead time issue, capacity constraint
2. Submit supply recovery plan with specific ASIN-level delivery commitments
3. Prioritize top 20 revenue ASINs for stock recovery
4. Increase safety stock by 20% for recovered ASINs

### Long-term Actions (1-3 months)
1. Improve demand forecasting accuracy (implement statistical forecasting)
2. Reduce lead time through sourcing optimization
3. Establish buffer inventory for top 50 ASINs
4. Implement automated PO monitoring and alerting

## Inventory Health Optimization

### Excess Inventory Reduction
1. **Identify excess ASINs**: Sell-through rate < 40% over 90 days
2. **Reduce PO quantities**: Align future orders with actual demand
3. **Create marketing push**: Increase advertising or promotions to clear excess
4. **Removal orders**: For 365+ day aging inventory, submit removal orders to avoid LTSF

### Stranded Inventory Resolution
1. **Identify stranded ASINs**: Check Vendor Central stranded inventory report
2. **Fix listing issues**: Resolve suppressions, pricing errors, compliance issues
3. **Timeline**: Target resolution within 7 days of identification
4. **Prevention**: Regular catalog health audits (weekly)

### Aging Inventory Management
1. **0-90 days**: Healthy - maintain normal operations
2. **91-180 days**: Monitor - review forecast accuracy and reduce future orders
3. **181-271 days**: Action required - increase promotions, create removal plan
4. **272+ days**: Critical - submit removal orders to avoid long-term storage fees
5. **365+ days**: Immediate action - removal or disposal required, fees accruing

## Vendor Compliance
- **PO Acknowledgment**: Within 24 hours (target 4 hours)
- **PO Shipment**: Within agreed lead time window
- **Carton Labeling**: ASN (Advance Shipment Notice) compliance required
- **Routing Guide Compliance**: Follow Amazon routing instructions to avoid chargebacks
- **Chargeback Prevention**: Monitor compliance scorecard weekly, address issues within 48 hours
