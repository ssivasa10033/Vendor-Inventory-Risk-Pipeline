# Databricks notebook source
# MAGIC %md
# MAGIC # Vendor & inventory risk: explore the gold tables
# MAGIC Run after the `vendor-inventory-pipeline` job. Everything below reads the Delta tables the job wrote.

# COMMAND ----------
dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "vendor_inventory")
cs = f"{dbutils.widgets.get('catalog')}.{dbutils.widgets.get('schema')}"

# COMMAND ----------
# MAGIC %md ## 1. The mess we started with (bronze)

# COMMAND ----------
display(spark.sql(f"""
  SELECT vendor_name, COUNT(*) AS invoice_lines
  FROM {cs}.bronze_invoices GROUP BY vendor_name ORDER BY vendor_name"""))

# COMMAND ----------
# MAGIC %md ## 2. What got quarantined, and why

# COMMAND ----------
display(spark.sql(f"""
  SELECT _source_table, _reason, COUNT(*) AS rows
  FROM {cs}.silver_quarantine GROUP BY ALL ORDER BY rows DESC"""))

# COMMAND ----------
# MAGIC %md ## 3. Vendor risk

# COMMAND ----------
display(spark.sql(f"""
  SELECT v.vendor_name, r.risk_score, r.risk_tier, r.top_risk_driver,
         r.on_time_rate, r.fill_rate, r.spend_share, r.price_change_yoy, r.overbilled_12m
  FROM {cs}.gold_vendor_risk r JOIN {cs}.gold_dim_vendor v USING (vendor_id)
  ORDER BY r.risk_score DESC"""))

# COMMAND ----------
# MAGIC %md ## 4. What needs action in inventory

# COMMAND ----------
display(spark.sql(f"""
  SELECT item_name, abc_class, stock_status, round(days_of_cover, 1) AS days_of_cover,
         round(reorder_point, 1) AS reorder_point, round(waste_rate, 3) AS waste_rate,
         emergency_buys_12m, round(item_risk_score, 1) AS item_risk_score, recommended_action
  FROM {cs}.gold_inventory_health
  WHERE recommended_action <> 'No action'
  ORDER BY item_risk_score DESC"""))

# COMMAND ----------
# MAGIC %md ## 5. Data quality results from the last run

# COMMAND ----------
display(spark.table(f"{cs}.gold_dq_results"))
