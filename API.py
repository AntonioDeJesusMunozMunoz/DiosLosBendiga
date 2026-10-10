#Estos endpoints pegarlos donde estaran los demas endpoints
# main.py
from fastapi import FastAPI
import fastapi
app = FastAPI()

router = FastAPI.APIRouter()
@router.GET("/work-orders/{code}/materials")
def work_order():
    pass

@router.GET("/shortages")
def work_order():
    pass


app.include_router(router)