import os
from fastapi.testclient import TestClient
from app import main

def test_crud_and_automation(tmp_path, monkeypatch):
 monkeypatch.setattr(main,'DB_PATH',tmp_path/'test.db')
 with TestClient(main.app) as client:
  r=client.get('/api/health');assert r.status_code==200
  overview=client.get('/api/meta/overview').json()
  assert overview['counts']['projects']>=2
  r=client.post('/api/tasks',json={'data':{'title':'Automated test task','priority':'low'}})
  assert r.status_code==201,r.text
  task=r.json();assert task['status']=='backlog'
  assert client.patch(f"/api/tasks/{task['id']}",json={'data':{'status':'done'}}).json()['status']=='done'
  assert client.get('/api/tasks?q=Automated').json()['total']==1
  rule=client.post('/api/automations',json={'data':{'name':'Critical priority rule','trigger_event':'tasks.created','condition_field':'status','condition_value':'todo','action':'set_priority','action_value':'critical'}})
  assert rule.status_code==201,rule.text
  result=client.post('/api/tasks',json={'data':{'title':'Trigger rule','status':'todo','priority':'low'}})
  assert result.status_code==201,result.text
  assert result.json()['priority']=='critical'
  assert client.get('/api/meta/export').json()['tasks']
  assert client.get('/api/meta/export?format=csv').headers['content-type']=='application/zip'
  assert client.delete(f"/api/tasks/{task['id']}").json()['deleted']
  assert client.patch('/api/tasks/999999',json={'data':{'status':'done'}}).status_code==404
  assert client.post('/api/tasks',json={'data':{'title':'X','evil':'oops'}}).status_code==422
