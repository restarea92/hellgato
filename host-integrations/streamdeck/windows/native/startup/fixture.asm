option casemap:none
EXTERN FixtureFooter:PROC
.code
FixtureCtor PROC
    mov edx, 4
    mov r8d, 2
    mov eax, edx
    imul eax, r8d
    ret
FixtureCtor ENDP

FixtureGate PROC FRAME
    sub rsp, 28h
    .allocstack 28h
    .endprolog
    call FixtureFooter
    add rsp, 28h
    ret
FixtureGate ENDP
END
