package com.sky.service.impl;

import com.sky.context.BaseContext;
import com.sky.entity.AddressBook;
import com.sky.exception.AddressBookBusinessException;
import com.sky.mapper.AddressBookMapper;
import com.sky.service.AddressBookService;
import com.sky.service.UserLockService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.List;

@Service
public class AddressBookServiceImpl implements AddressBookService {
    private final AddressBookMapper addresses;
    private final UserLockService userLock;
    public AddressBookServiceImpl(AddressBookMapper addresses,UserLockService userLock) {
        this.addresses=addresses; this.userLock=userLock;
    }
    @Override public List<AddressBook> list(AddressBook filter) {
        filter.setUserId(BaseContext.getCurrentId()); return addresses.list(filter);
    }
    @Override @Transactional public void save(AddressBook address) {
        address.setUserId(userLock.lockCurrentUser()); address.setId(null); address.setIsDefault(0);
        validate(address); addresses.insert(address);
    }
    @Override public AddressBook getById(Long id) {
        AddressBook address=addresses.getOwned(id,BaseContext.getCurrentId());
        if (address==null) { throw new AddressBookBusinessException("地址不存在"); }
        return address;
    }
    @Override @Transactional public void update(AddressBook address) {
        userLock.lockCurrentUser(); AddressBook owned=getById(address.getId());
        address.setUserId(owned.getUserId()); address.setIsDefault(owned.getIsDefault());
        if (address.getPhone()!=null && !address.getPhone().matches("[0-9]{11}")) {
            throw new AddressBookBusinessException("手机号需为11位数字");
        }
        addresses.update(address);
    }
    @Override @Transactional public void setDefault(AddressBook request) {
        userLock.lockCurrentUser(); AddressBook owned=getById(request.getId());
        AddressBook reset=new AddressBook(); reset.setUserId(owned.getUserId()); reset.setIsDefault(0);
        addresses.updateIsDefaultByUserId(reset);
        AddressBook selected=new AddressBook(); selected.setId(owned.getId()); selected.setUserId(owned.getUserId()); selected.setIsDefault(1);
        addresses.update(selected);
    }
    @Override @Transactional public void deleteById(Long id) {
        Long userId=userLock.lockCurrentUser(); getById(id); addresses.deleteOwned(id,userId);
    }
    private void validate(AddressBook address) {
        if (address.getConsignee()==null || address.getConsignee().trim().isEmpty() || address.getConsignee().length()>50
                || address.getPhone()==null || !address.getPhone().matches("[0-9]{11}")
                || address.getDetail()==null || address.getDetail().trim().isEmpty() || address.getDetail().length()>200) {
            throw new AddressBookBusinessException("请填写收件人、11位手机号和详细地址");
        }
    }
}
