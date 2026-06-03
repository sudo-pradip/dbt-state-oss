select customer_id, sum(amount) as total_amount
from {{ ref('stg_orders') }}
where status = 'complete'
group by customer_id
