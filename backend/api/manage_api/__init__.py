#定义manage_api的父层api，便于管理所有管理端api，降低耦合度
from fastapi import APIRouter

manage_api=APIRouter(prefix='/manage',tags=['manage'])
