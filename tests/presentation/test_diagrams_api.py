def test_diagram_crud_flow(client):
    # 1. Create diagram
    resp = client.post("/api/diagrams", json={"name": "Sistema Peluqueria", "description": "Diagrama de prueba"})
    assert resp.status_code == 201
    data = resp.json()
    diagram_id = data["id"]
    assert data["name"] == "Sistema Peluqueria"
    assert data["version"] == 1
    assert len(data["classes"]) == 0

    # 2. Get diagram by ID
    resp_get = client.get(f"/api/diagrams/{diagram_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["id"] == diagram_id

    # 3. List diagrams
    resp_list = client.get("/api/diagrams")
    assert resp_list.status_code == 200
    assert any(d["id"] == diagram_id for d in resp_list.json())

    # 4. Dispatch CREATE_CLASS command
    cmd_payload = {
        "command": {
            "command_type": "CREATE_CLASS",
            "name": "Cliente",
            "position": {"x": 100, "y": 200},
        }
    }
    resp_cmd = client.post(f"/api/diagrams/{diagram_id}/commands", json=cmd_payload)
    assert resp_cmd.status_code == 200
    cmd_data = resp_cmd.json()
    assert cmd_data["success"] is True
    assert cmd_data["version"] == 2
    classes = cmd_data["document"]["classes"]
    assert len(classes) == 1
    assert classes[0]["name"] == "Cliente"
    cliente_id = classes[0]["id"]

    # 5. Dispatch ADD_ATTRIBUTE command
    attr_payload = {
        "command": {
            "command_type": "ADD_ATTRIBUTE",
            "class_id": cliente_id,
            "name": "telefono",
            "type": "String",
            "nullable": False,
        }
    }
    resp_attr = client.post(f"/api/diagrams/{diagram_id}/commands", json=attr_payload)
    assert resp_attr.status_code == 200
    attr_data = resp_attr.json()
    assert attr_data["version"] == 3
    cliente_attrs = attr_data["document"]["classes"][0]["attributes"]
    assert len(cliente_attrs) == 2
    assert any(a["name"] == "telefono" for a in cliente_attrs)

    # 6. Test domain validation error: duplicate class name
    dup_payload = {
        "command": {
            "command_type": "CREATE_CLASS",
            "name": "Cliente",
        }
    }
    resp_dup = client.post(f"/api/diagrams/{diagram_id}/commands", json=dup_payload)
    assert resp_dup.status_code == 400
    assert "already exists" in resp_dup.json()["detail"]

    # 7. Delete diagram
    resp_del = client.delete(f"/api/diagrams/{diagram_id}")
    assert resp_del.status_code == 204

    # 8. Confirm deleted
    resp_check = client.get(f"/api/diagrams/{diagram_id}")
    assert resp_check.status_code == 404

