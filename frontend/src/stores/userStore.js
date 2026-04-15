import { defineStore } from "pinia";

export const userInfo = defineStore(
    'user',
    ()=>{
        //定义用户数据
        const userInfo  = ref()
       return {
        userInfo
}  
})