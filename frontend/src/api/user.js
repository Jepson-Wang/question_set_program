import { httpInstance } from "../utils/http";
//注册接口
export const getUserRegistAPI = (data)=>{
    return httpInstance({
        url:'/api/login/register',
        method:'POST',
        data
    })
}
//登录接口
export const getUserLoginAPI = (data)=>{
    return httpInstance({
        url:'/api/login/login',
        method:'POST',
        data
    })
}
